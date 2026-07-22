# AGENTS.md

Repository guidelines and instructions for AI coding agents.

## Project Overview

`antigravity-chat` is a lightweight web UI for the `agy` (Antigravity) CLI. It provides a simple chat interface over a FastAPI backend that shells out to `agy`.

- **Backend**: FastAPI (`app.py`), Python 3.x
- **Frontend**: Single-page static HTML/JS (`static/index.html`)
- **Server Entrypoint**: `./start.sh` (runs Uvicorn on port `3121` by default)

## Key Technical Conventions

- **Stateless Execution**: The backend re-injects conversation history into prompt turns to prevent persistent workspace state bleed in `agy`.
- **Subprocess Execution**: Commands interact with `agy` using flags such as `--print` and `--dangerously-skip-permissions`.
- **Dependencies**: Managed within standard Python virtual environments (`.venv`).

## Code Guidelines

- Keep the architecture simple and database-free.
- Maintain compatibility for OpenAI-style endpoints (`/v1/chat/completions`, `/v1/models`).
- Ensure frontend assets in `static/` remain dependency-free and lightweight (vanilla HTML/JS/CSS).
