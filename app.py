"""antigravity-chat — a minimal web chat UI backed by the `agy` (antigravity) CLI.

Self-contained FastAPI app. It shells `agy --print` headlessly using `--output-format stream-json`
and streams real-time token events to a lightweight web UI, supporting stateful conversations.

This is NOT the OpenRouter proxy on :3120 — it talks to the real antigravity CLI.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import secrets
import time
import uuid
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from pydantic import BaseModel

logger = logging.getLogger("antigravity-chat")

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
AGY_OUTPUT_FORMAT = os.getenv("AGY_OUTPUT_FORMAT", "stream-json")
AGY_PERSIST_CONVERSATIONS = os.getenv("AGY_PERSIST_CONVERSATIONS", "true").lower() in ("1", "true", "yes")
CONVERSATIONS_FILE = Path(os.getenv("AGY_CONVERSATIONS_FILE", str(Path(__file__).parent / ".antigravity_conversations.json")))

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

app = FastAPI(title="antigravity-chat", version="1.1.0")

# --------------------------------------------------------------------------- #
# Conversation persistence layer
# --------------------------------------------------------------------------- #

_conversations_lock = asyncio.Lock()


def _sanitize_token(val: str | None, default: str | None = None) -> str | None:
    if not val or not isinstance(val, str):
        return default
    clean = "".join(c for c in val if c.isalnum() or c in ("-", "_")).strip()
    return clean if clean else default


def _read_conversations() -> dict:
    if not CONVERSATIONS_FILE.exists():
        return {}
    try:
        return json.loads(CONVERSATIONS_FILE.read_text())
    except Exception as exc:
        logger.warning(f"Failed to read conversation mapping file {CONVERSATIONS_FILE}: {exc}")
        return {}


def _write_conversations(data: dict) -> None:
    try:
        tmp_file = CONVERSATIONS_FILE.with_suffix(".json.tmp")
        tmp_file.write_text(json.dumps(data, indent=2))
        tmp_file.replace(CONVERSATIONS_FILE)
    except Exception as exc:
        logger.warning(f"Failed to write conversation mapping file {CONVERSATIONS_FILE}: {exc}")


def save_conversation(user_id: str, conversation_id: str, title: str | None = None) -> dict:
    """Save user_id -> conversation_id mapping to JSON file."""
    uid = _sanitize_token(user_id, "default") or "default"
    cid = _sanitize_token(conversation_id)
    if not cid:
        return {}
    data = _read_conversations()
    existing = data.get(uid) if isinstance(data.get(uid), dict) else {}
    resolved_title = title or existing.get("title") or "Conversation"
    data[uid] = {
        "conversation_id": cid,
        "title": resolved_title,
        "updated_at": time.time(),
    }
    _write_conversations(data)
    return data[uid]


async def save_conversation_async(user_id: str, conversation_id: str, title: str | None = None) -> dict:
    async with _conversations_lock:
        return await asyncio.to_thread(save_conversation, user_id, conversation_id, title)


def get_conversation_id(user_id: str = "default") -> str | None:
    """Retrieve saved conversation ID for a user."""
    uid = _sanitize_token(user_id, "default") or "default"
    data = _read_conversations()
    info = data.get(uid)
    if isinstance(info, dict):
        return info.get("conversation_id")
    return None


async def get_conversation_id_async(user_id: str = "default") -> str | None:
    async with _conversations_lock:
        return await asyncio.to_thread(get_conversation_id, user_id)


def clear_conversation(user_id: str = "default") -> None:
    """Clear conversation mapping for a user."""
    uid = _sanitize_token(user_id, "default") or "default"
    data = _read_conversations()
    if uid in data:
        del data[uid]
        _write_conversations(data)


async def clear_conversation_async(user_id: str = "default") -> None:
    async with _conversations_lock:
        await asyncio.to_thread(clear_conversation, user_id)


def list_conversations() -> dict:
    """List stored conversation mappings."""
    return _read_conversations()


async def list_conversations_async() -> dict:
    async with _conversations_lock:
        return await asyncio.to_thread(list_conversations)

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
    conversation_id: str | None = None
    continue_conversation: bool | None = None
    user_id: str | None = "default"


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


async def _resolve_prompt_and_session(req: ChatRequest) -> tuple[str, str | None, bool, str]:
    user_id = _sanitize_token(req.user_id, "default") or "default"
    cid = _sanitize_token(req.conversation_id)

    if not AGY_PERSIST_CONVERSATIONS:
        cid = None

    if AGY_PERSIST_CONVERSATIONS and not cid and req.continue_conversation:
        cid = await get_conversation_id_async(user_id)

    if cid:
        prompt = req.messages[-1].content if req.messages else ""
        continue_last = False
    elif req.continue_conversation:
        prompt = req.messages[-1].content if req.messages else ""
        continue_last = True
    else:
        prompt = _render_prompt(req.messages, req.system)
        continue_last = False

    return prompt, cid, continue_last, user_id


def _build_argv(
    model: str | None,
    prompt: str = "",
    conversation_id: str | None = None,
    continue_last: bool = False,
) -> list[str]:
    argv = [AGY_BIN, "-p", "--dangerously-skip-permissions"]
    if AGY_OUTPUT_FORMAT:
        argv += ["--output-format", AGY_OUTPUT_FORMAT]
    if AGY_SANDBOX:
        argv.append("--sandbox")
    if model and model.strip() and model != "default":
        argv += ["--model", model]
    clean_cid = _sanitize_token(conversation_id)
    if clean_cid:
        argv += ["--conversation", clean_cid]
    elif continue_last:
        argv.append("--continue")
    argv += ["--print-timeout", f"{AGY_TIMEOUT}s"]
    # Append prompt as positional arg — passing via stdin causes timeout on some setups.
    if prompt:
        argv.append(prompt)
    return argv


def _parse_ndjson_line(line_str: str) -> list[dict]:
    events: list[dict] = []
    line_clean = line_str.strip()
    if not line_clean:
        return events
    try:
        obj = json.loads(line_clean)
        ev_type = obj.get("event") or obj.get("type")
        
        if ev_type == "init":
            init_obj = obj.get("init")
            cid = obj.get("conversation_id") or (
                init_obj.get("conversation_id") if isinstance(init_obj, dict) else None
            )
            events.append({"event": "init", "conversation_id": cid})
            
        elif ev_type in ("tool_call", "tool"):
            tool_name = obj.get("tool") or obj.get("name") or obj.get("tool_name")
            args = obj.get("args") or obj.get("input")
            status = obj.get("status") or "executing"
            events.append({"event": "tool", "tool": tool_name, "args": args, "status": status})

        elif ev_type == "step_update":
            step = obj.get("step_update", {})
            stype = step.get("step_type")
            if stype == "agent_response":
                delta = step.get("text_delta") or step.get("delta")
                if delta:
                    events.append({"event": "delta", "delta": delta})
                thinking = step.get("thinking")
                if thinking:
                    events.append({"event": "thinking", "thinking": thinking})
            elif stype in ("tool", "tool_call") or "tool" in step:
                tool_name = step.get("tool") or step.get("name")
                args = step.get("args") or step.get("input")
                status = step.get("status") or "executing"
                events.append({"event": "tool", "tool": tool_name, "args": args, "status": status})
            else:
                delta = step.get("text_delta") or step.get("delta")
                if delta:
                    events.append({"event": "delta", "delta": delta})
                    
        elif ev_type == "result":
            res = obj.get("result", {})
            usage = res.get("usage") or obj.get("usage")
            status = res.get("status") or obj.get("status") or "completed"
            events.append({"event": "result", "usage": usage, "status": status})
            
        else:
            delta = obj.get("text_delta") or obj.get("delta") or obj.get("text")
            if delta:
                events.append({"event": "delta", "delta": delta})
            cid = obj.get("conversation_id")
            if cid:
                events.append({"event": "init", "conversation_id": cid})
    except json.JSONDecodeError:
        events.append({"event": "delta", "delta": line_str + "\n"})
    return events


async def _stream_agy(
    prompt: str,
    model: str | None,
    cwd: str | None = None,
    conversation_id: str | None = None,
    continue_last: bool = False,
    user_id: str = "default",
):
    """Run agy and yield structured events (dict) as NDJSON or text chunks arrive."""
    argv = _build_argv(
        model,
        prompt=prompt,
        conversation_id=conversation_id,
        continue_last=continue_last,
    )
    target_cwd = _within_allowed(cwd) if cwd else None
    if not target_cwd:
        target_cwd = str(SCRATCH)

    proc = await asyncio.create_subprocess_exec(
        *argv,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=target_cwd,
    )

    assert proc.stdout is not None
    buffered_line = ""

    try:
        while True:
            chunk = await asyncio.wait_for(proc.stdout.read(1024), timeout=AGY_TIMEOUT)
            if not chunk:
                break
            text = chunk.decode(errors="replace")

            if AGY_OUTPUT_FORMAT == "stream-json":
                buffered_line += text
                lines = buffered_line.split("\n")
                buffered_line = lines.pop()
                for line in lines:
                    parsed_events = _parse_ndjson_line(line)
                    for ev in parsed_events:
                        if ev.get("event") == "init" and ev.get("conversation_id") and AGY_PERSIST_CONVERSATIONS:
                            await save_conversation_async(user_id, ev["conversation_id"])
                        yield ev
            else:
                yield {"event": "delta", "delta": text}

        if buffered_line and buffered_line.strip():
            if AGY_OUTPUT_FORMAT == "stream-json":
                parsed_events = _parse_ndjson_line(buffered_line)
                for ev in parsed_events:
                    if ev.get("event") == "init" and ev.get("conversation_id") and AGY_PERSIST_CONVERSATIONS:
                        await save_conversation_async(user_id, ev["conversation_id"])
                    yield ev
            else:
                yield {"event": "delta", "delta": buffered_line}

    except asyncio.TimeoutError:
        proc.kill()
        yield {"event": "error", "error": f"antigravity-chat: timed out after {AGY_TIMEOUT}s"}
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
    return {
        "status": "ok",
        "backend": "agy",
        "sandbox": AGY_SANDBOX,
        "auth": _AUTH_ENABLED,
        "output_format": AGY_OUTPUT_FORMAT,
        "persist_conversations": AGY_PERSIST_CONVERSATIONS,
    }


@app.get("/api/fs")
async def fs(path: str | None = None) -> dict:
    return browse_fs(path)


@app.get("/api/conversations")
async def conversations(user_id: str = "default") -> dict:
    uid = _sanitize_token(user_id, "default") or "default"
    cid = await get_conversation_id_async(uid)
    return {"user_id": uid, "conversation_id": cid}


@app.delete("/api/conversations")
async def delete_conversation(user_id: str = "default") -> dict:
    uid = _sanitize_token(user_id, "default") or "default"
    await clear_conversation_async(uid)
    return {"status": "cleared", "user_id": uid}


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
    
    prompt, cid, continue_last, user_id = await _resolve_prompt_and_session(req)

    async def event_gen():
        yield f"data: {json.dumps({'event': 'start'})}\n\n"
        try:
            async for ev in _stream_agy(
                prompt=prompt,
                model=req.model,
                cwd=req.cwd,
                conversation_id=cid,
                continue_last=continue_last,
                user_id=user_id,
            ):
                yield f"data: {json.dumps(ev)}\n\n"
        except Exception as exc:  # noqa: BLE001
            yield f"data: {json.dumps({'event': 'error', 'error': str(exc)})}\n\n"
        yield f"data: {json.dumps({'event': 'done'})}\n\n"

    return StreamingResponse(event_gen(), media_type="text/event-stream")


# --- Bonus: OpenAI-compatible endpoint so other tools can reuse this too ----- #

@app.post("/v1/chat/completions")
async def openai_chat(req: ChatRequest) -> JSONResponse:
    if not req.messages:
        raise HTTPException(status_code=422, detail="messages must be non-empty")
    
    prompt, cid, continue_last, user_id = await _resolve_prompt_and_session(req)

    parts: list[str] = []
    error_msg: str | None = None
    async for ev in _stream_agy(
        prompt=prompt,
        model=req.model,
        cwd=req.cwd,
        conversation_id=cid,
        continue_last=continue_last,
        user_id=user_id,
    ):
        if ev.get("event") == "delta" and ev.get("delta"):
            parts.append(ev["delta"])
        elif ev.get("event") == "error" and ev.get("error"):
            error_msg = ev["error"]

    if error_msg:
        raise HTTPException(status_code=500, detail=error_msg)

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
