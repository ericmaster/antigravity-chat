# AGENTS.md

Repository guidelines and instructions for AI coding agents.

## Project Overview

`antigravity-chat` is a lightweight web UI for the `agy` (Antigravity) CLI. It provides a simple chat interface over a FastAPI backend that shells out to `agy`.

- **Backend**: FastAPI (`app.py`), Python 3.x
- **Frontend**: Single-page static HTML/JS (`static/index.html`)
- **Server Entrypoint**: `./start.sh` (runs Uvicorn on port `3121` by default)
- **Background Daemon (Optional)**: Can run as an optional systemd user service (`~/.config/systemd/user/antigravity-chat.service`, see template [`antigravity-chat.service.example`](antigravity-chat.service.example)). Managed via `systemctl --user {status|restart|stop|start} antigravity-chat`.

## Key Technical Conventions

- **Stateful & Token Streaming Execution**: Uses `agy --output-format stream-json` to stream real-time token events and retain session state via `conversation_id` (`--conversation <id>`). For new sessions or when persistence is disabled, falls back to rendering conversation history.
- **Subprocess Execution**: Commands interact with `agy` using flags such as `--print`, `--output-format stream-json`, and `--dangerously-skip-permissions`.
- **Dependencies**: Managed within standard Python virtual environments (`.venv`).
- **Process Supervision**: Works directly when executed via `./start.sh` or through `systemd` user service units.


## Code Guidelines

- Keep the architecture simple and database-free.
- Maintain compatibility for OpenAI-style endpoints (`/v1/chat/completions`, `/v1/models`).
- Ensure frontend assets in `static/` remain dependency-free and lightweight (vanilla HTML/JS/CSS).
