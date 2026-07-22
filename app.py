"""antigravity-chat — a minimal web chat UI backed by the `agy` (antigravity) CLI.

Self-contained FastAPI app. It shells `agy --print` headlessly and streams the
result to a tiny single-page chat UI. Stateless: full conversation history is
re-injected each turn (avoids agy's persistent-workspace context bleed).

This is NOT the OpenRouter proxy on :3120 — it talks to the real antigravity CLI.
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import secrets
import time
import uuid
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from pydantic import BaseModel

# --------------------------------------------------------------------------- #
# Config
# --------------------------------------------------------------------------- #

def _load_env() -> None:
    env_file = Path(__file__).parent / ".env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip("'\""))


_load_env()

PORT = int(os.getenv("PORT", "3121"))
AGY_BIN = os.getenv("AGY_BIN", "agy")
AGY_TIMEOUT = int(os.getenv("AGY_TIMEOUT", "300"))  # seconds per turn
AGY_SANDBOX = os.getenv("AGY_SANDBOX", "0") not in ("0", "false", "")
# Neutral working dir so agy does not pull in unrelated repo context.
SCRATCH = Path(os.getenv("AGY_SCRATCH", str(Path(__file__).parent / "scratch")))
SCRATCH.mkdir(parents=True, exist_ok=True)
STATIC = Path(__file__).parent / "static"

HOME = os.path.expanduser("~")
DEFAULT_ALLOWED_ROOTS = f"{HOME}"
_allowed_raw = os.getenv("AGY_ALLOWED_ROOTS", DEFAULT_ALLOWED_ROOTS)
ALLOWED_ROOTS = [
    os.path.realpath(os.path.expanduser(p.strip()))
    for p in _allowed_raw.split(":")
    if p.strip()
]

DEFAULT_SYSTEM = (
    "You are a helpful, concise assistant chatting with a user in a web UI. "
    "Answer the user's last message directly in Markdown. Do not use tools, "
    "do not read or write files, and do not run shell commands unless the user "
    "explicitly asks you to."
)

app = FastAPI(title="antigravity-chat", version="1.0.0")

# --------------------------------------------------------------------------- #
# Path jailing & filesystem browsing
# --------------------------------------------------------------------------- #

def _within_allowed(path: str | None) -> str | None:
    if not path or not path.strip():
        return None
    rp = os.path.realpath(os.path.expanduser(path.strip()))
    for root in ALLOWED_ROOTS:
        if rp == root or rp.startswith(root + os.sep):
            return rp
    return None


def _is_project_dir(path: str) -> bool:
    return any(
        os.path.exists(os.path.join(path, m))
        for m in (".git", "CLAUDE.md", "AGENTS.md", "package.json", "pyproject.toml", "README.md")
    )


def browse_fs(path: str | None) -> dict:
    """List immediate subdirectories of a jailed path (or allowed roots if path is empty)."""
    if not path:
        entries = [
            {
                "name": os.path.basename(r) or r,
                "path": r,
                "is_project": _is_project_dir(r),
            }
            for r in ALLOWED_ROOTS
            if os.path.isdir(r)
        ]
        return {"path": "", "parent": None, "up": None, "roots": ALLOWED_ROOTS, "entries": entries}

    rp = _within_allowed(path)
    if rp is None or not os.path.isdir(rp):
        raise HTTPException(status_code=400, detail="Path outside allowed roots or not a directory")

    entries = []
    try:
        filenames = sorted(os.listdir(rp))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to list directory: {exc}")

    for name in filenames:
        if name.startswith("."):
            continue
        full = os.path.join(rp, name)
        if os.path.isdir(full):
            entries.append({"name": name, "path": full, "is_project": _is_project_dir(full)})

    parent = os.path.dirname(rp)
    up = parent if _within_allowed(parent) is not None else ""
    return {"path": rp, "parent": up, "up": up, "roots": ALLOWED_ROOTS, "entries": entries}

# --------------------------------------------------------------------------- #
# HTTP Basic Auth (defense-in-depth, behind Cloudflare Access)
# --------------------------------------------------------------------------- #

BASIC_AUTH_USER = os.getenv("BASIC_AUTH_USER", "")
BASIC_AUTH_PASS = os.getenv("BASIC_AUTH_PASS", "")
_AUTH_ENABLED = bool(BASIC_AUTH_USER and BASIC_AUTH_PASS)
# Paths reachable without credentials (tunnel/Access health checks).
_AUTH_EXEMPT = {"/healthz"}


@app.middleware("http")
async def _basic_auth(request: Request, call_next):
    if _AUTH_ENABLED and request.url.path not in _AUTH_EXEMPT:
        header = request.headers.get("authorization", "")
        ok = False
        if header.startswith("Basic "):
            try:
                user, _, pw = base64.b64decode(header[6:]).decode().partition(":")
                ok = secrets.compare_digest(user, BASIC_AUTH_USER) and \
                    secrets.compare_digest(pw, BASIC_AUTH_PASS)
            except Exception:  # noqa: BLE001
                ok = False
        if not ok:
            return Response(
                status_code=401,
                headers={"WWW-Authenticate": 'Basic realm="antigravity-chat"'},
                content="Authentication required",
            )
    return await call_next(request)


_models_cache: tuple[float, list[str]] | None = None
_MODELS_TTL = 300


# --------------------------------------------------------------------------- #
# Models
# --------------------------------------------------------------------------- #

class Message(BaseModel):
    role: str
    content: str


class ChatRequest(BaseModel):
    messages: list[Message]
    model: str | None = None
    system: str | None = None
    cwd: str | None = None


# --------------------------------------------------------------------------- #
# agy invocation
# --------------------------------------------------------------------------- #

def _render_prompt(messages: list[Message], system: str | None) -> str:
    """Flatten a chat history into a single prompt for agy print mode."""
    sys_txt = (system or DEFAULT_SYSTEM).strip()
    lines = [sys_txt, "", "--- Conversation so far ---"]
    for m in messages:
        role = {"user": "User", "assistant": "Assistant", "system": "System"}.get(
            m.role, m.role.capitalize()
        )
        lines.append(f"{role}: {m.content}")
    lines.append("--- End conversation ---")
    lines.append("")
    lines.append("Reply as the Assistant to the last User message. "
                 "Output only your reply, with no role prefix.")
    return "\n".join(lines)


def _build_argv(model: str | None) -> list[str]:
    argv = [AGY_BIN, "-p", "--dangerously-skip-permissions"]
    if AGY_SANDBOX:
        argv.append("--sandbox")
    if model and model.strip() and model != "default":
        argv += ["--model", model]
    # prompt is fed via stdin to avoid arg-length limits and quoting issues
    argv += ["--print-timeout", f"{AGY_TIMEOUT}s"]
    return argv


async def _stream_agy(prompt: str, model: str | None, cwd: str | None = None):
    """Run agy and yield stdout text chunks as they arrive."""
    argv = _build_argv(model)
    target_cwd = _within_allowed(cwd) if cwd else None
    if not target_cwd:
        target_cwd = str(SCRATCH)

    proc = await asyncio.create_subprocess_exec(
        *argv,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=target_cwd,
    )
    # Feed the prompt then close stdin.
    assert proc.stdin is not None
    proc.stdin.write(prompt.encode())
    await proc.stdin.drain()
    proc.stdin.close()

    assert proc.stdout is not None
    try:
        while True:
            chunk = await asyncio.wait_for(proc.stdout.read(1024), timeout=AGY_TIMEOUT)
            if not chunk:
                break
            yield chunk.decode(errors="replace")
    except asyncio.TimeoutError:
        proc.kill()
        yield "\n\n_[antigravity-chat: timed out]_"
        return
    finally:
        await proc.wait()


# --------------------------------------------------------------------------- #
# Routes
# --------------------------------------------------------------------------- #

@app.get("/")
async def index() -> FileResponse:
    return FileResponse(STATIC / "index.html")


@app.get("/healthz")
async def healthz() -> dict:
    return {"status": "ok", "backend": "agy", "sandbox": AGY_SANDBOX, "auth": _AUTH_ENABLED}


@app.get("/api/fs")
async def fs(path: str | None = None) -> dict:
    return browse_fs(path)


@app.get("/api/models")
async def models() -> dict:
    global _models_cache
    now = time.monotonic()
    if _models_cache and now - _models_cache[0] < _MODELS_TTL:
        return {"models": _models_cache[1]}
    
    max_retries = 3
    base_delay = 0.5
    names: list[str] = []
    
    for attempt in range(max_retries):
        try:
            proc = await asyncio.create_subprocess_exec(
                AGY_BIN, "models",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            out, _ = await asyncio.wait_for(proc.communicate(), timeout=30)
            names = [ln.strip() for ln in out.decode().splitlines() if ln.strip()]
            if names:
                break
        except Exception:
            names = []
        
        if attempt < max_retries - 1:
            await asyncio.sleep(base_delay * (2 ** attempt))
    
    if names:
        _models_cache = (now, names)
    return {"models": names}


@app.post("/api/chat")
async def chat(req: ChatRequest):
    """SSE stream of the assistant reply for the custom UI."""
    if not req.messages:
        raise HTTPException(status_code=422, detail="messages must be non-empty")
    prompt = _render_prompt(req.messages, req.system)

    async def event_gen():
        yield f"data: {json.dumps({'event': 'start'})}\n\n"
        try:
            async for chunk in _stream_agy(prompt, req.model, req.cwd):
                yield f"data: {json.dumps({'delta': chunk})}\n\n"
        except Exception as exc:  # noqa: BLE001
            yield f"data: {json.dumps({'error': str(exc)})}\n\n"
        yield f"data: {json.dumps({'event': 'done'})}\n\n"

    return StreamingResponse(event_gen(), media_type="text/event-stream")


# --- Bonus: OpenAI-compatible endpoint so other tools can reuse this too ----- #

@app.post("/v1/chat/completions")
async def openai_chat(req: ChatRequest) -> JSONResponse:
    if not req.messages:
        raise HTTPException(status_code=422, detail="messages must be non-empty")
    prompt = _render_prompt(req.messages, req.system)
    parts: list[str] = []
    async for chunk in _stream_agy(prompt, req.model, req.cwd):
        parts.append(chunk)
    content = "".join(parts).strip()
    return JSONResponse({
        "id": f"agy-{uuid.uuid4().hex[:12]}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": req.model or "agy",
        "choices": [{
            "index": 0,
            "message": {"role": "assistant", "content": content},
            "finish_reason": "stop",
        }],
    })



@app.get("/v1/models")
async def openai_models() -> JSONResponse:
    m = await models()
    now = int(time.time())
    return JSONResponse({
        "object": "list",
        "data": [{"id": n, "object": "model", "created": now, "owned_by": "antigravity"}
                 for n in m["models"]] or [{"id": "agy", "object": "model",
                                            "created": now, "owned_by": "antigravity"}],
    })


def main() -> None:
    import uvicorn
    uvicorn.run("app:app", host="0.0.0.0", port=PORT, reload=False)


if __name__ == "__main__":
    main()
