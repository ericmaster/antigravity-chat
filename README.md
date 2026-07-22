# antigravity-chat

A **lightweight web chat UI for the real antigravity (`agy`) CLI**. No database,
no auth stack — a single FastAPI process + one static HTML page.

> Not to be confused with `antigravity-cli-proxy` on `:3120`. Despite its name,
> that service was repurposed (Phase 3) to forward to **OpenRouter** and serves
> agentmemory. This app talks to the actual `agy` binary.

## What it does

- Serves a minimal single-page chat at `/`.
- `POST /api/chat` — SSE stream of the assistant reply, produced by shelling
  `agy --print --dangerously-skip-permissions` and streaming stdout.
- `GET /api/models` — live model list from `agy models` (populates the picker).
- **Stateless**: the full conversation is re-injected into the prompt each turn,
  avoiding agy's persistent-workspace context bleed.
- **Bonus OpenAI-compatible endpoints** (`/v1/chat/completions`, `/v1/models`) so
  other OpenAI-protocol tools can reuse it — point their `base_url` at this port.

## Run

```bash
./start.sh                 # → http://0.0.0.0:3121
```

## Config (env vars)

| Var | Default | Description |
|-----|---------|-------------|
| `PORT` | `3121` | HTTP listen port |
| `AGY_BIN` | `agy` | CLI binary |
| `AGY_TIMEOUT` | `300` | Max seconds per turn |
| `AGY_SANDBOX` | `0` | Set `1` to run agy in `--sandbox` (recommended if exposed publicly) |
| `AGY_SCRATCH` | `./scratch` | Neutral cwd for agy (keeps unrelated repos out of context) |

## Notes / limits

- `agy --print` has no token-level streaming; output is flushed in chunks, so the
  UI "types" in bursts rather than per-token.
- Per-turn latency is whatever `agy` takes (subprocess + model). The `default`
  model uses agy's configured default; pick a `(Low)` Flash model for speed.
- Uses your antigravity subscription/quota — no API keys needed.
