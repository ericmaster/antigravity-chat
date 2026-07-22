#!/usr/bin/env bash
# Launch antigravity-chat (agy-backed web chat UI) with HTTP Basic Auth.
#
# Authenticates to Infisical via Universal Auth (machine-identity creds come from
# the environment / /etc/environment) and injects the basic-auth credentials
# (AGYCHAT_BASIC_AUTH_USER / AGYCHAT_BASIC_AUTH_PASS) as BASIC_AUTH_USER/PASS.
# Credentials are never written to disk or logged.
set -euo pipefail
cd "$(dirname "$0")"

if [ -f .env ]; then
  set -o allexport
  source .env
  set +o allexport
fi

export PORT="${PORT:-3121}"
export AGY_TIMEOUT="${AGY_TIMEOUT:-300}"
export AGY_SANDBOX="${AGY_SANDBOX:-0}"

# Check if PORT is currently in use
_OCCUPIED_PID=""
if command -v lsof >/dev/null 2>&1; then
  _OCCUPIED_PID=$(lsof -t -i:"$PORT" 2>/dev/null || true)
elif command -v fuser >/dev/null 2>&1; then
  _OCCUPIED_PID=$(fuser "$PORT"/tcp 2>/dev/null | xargs || true)
fi

if [ -z "$_OCCUPIED_PID" ]; then
  if ! ./.venv/bin/python -c "import socket, sys; s = socket.socket(); s.bind(('0.0.0.0', int(sys.argv[1])))" "$PORT" 2>/dev/null; then
    _OCCUPIED_PID="unknown"
  fi
fi

if [ -n "$_OCCUPIED_PID" ]; then
  echo "Port $PORT is currently in use (PID: $_OCCUPIED_PID)."
  if [ -t 0 ] || [ -t 1 ]; then
    read -rp "Would you like to kill the process on port $PORT and restart? [y/N] " _REPLY
    case "$_REPLY" in
      [yY][eE][sS]|[yY])
        echo "Stopping process on port $PORT..."
        if [ "$_OCCUPIED_PID" != "unknown" ]; then
          kill -9 $_OCCUPIED_PID 2>/dev/null || true
        elif command -v fuser >/dev/null 2>&1; then
          fuser -k -9 "$PORT"/tcp 2>/dev/null || true
        fi
        sleep 1
        ;;
      *)
        echo "Aborting."
        exit 1
        ;;
    esac
  else
    echo "Non-interactive session: cannot prompt to kill process on port $PORT. Aborting." >&2
    exit 1
  fi
fi


_CLIENT_ID="${INFISICAL_UNIVERSAL_AUTH_CLIENT_ID:-${INFISICAL_CLIENT_ID:-}}"
_CLIENT_SECRET="${INFISICAL_UNIVERSAL_AUTH_CLIENT_SECRET:-${INFISICAL_CLIENT_SECRET:-}}"
_DOMAIN="${INFISICAL_HOST_URL:-http://localhost:3080}"
_PID="${INFISICAL_PROJECT_ID:-${INFISICAL_PID:-}}"
_ENV="${INFISICAL_ENV:-dev}"

if [ -z "$_PID" ]; then
  echo "ERROR: Infisical Project ID (INFISICAL_PROJECT_ID) not found." >&2
  exit 1
fi


if [ -z "$_CLIENT_ID" ] || [ -z "$_CLIENT_SECRET" ]; then
  echo "ERROR: Infisical machine-identity creds not found." >&2
  exit 1
fi

INFISICAL_TOKEN=$(infisical login --method=universal-auth \
  --client-id="$_CLIENT_ID" --client-secret="$_CLIENT_SECRET" \
  --domain="$_DOMAIN" --plain --silent)
export INFISICAL_TOKEN

# Fetch only the two secrets we need (avoids injecting the whole project set).
BASIC_AUTH_USER=$(infisical secrets get AGYCHAT_BASIC_AUTH_USER \
  --projectId="$_PID" --env="$_ENV" --domain="$_DOMAIN" --plain --silent)
BASIC_AUTH_PASS=$(infisical secrets get AGYCHAT_BASIC_AUTH_PASS \
  --projectId="$_PID" --env="$_ENV" --domain="$_DOMAIN" --plain --silent)
export BASIC_AUTH_USER BASIC_AUTH_PASS

if [ -z "$BASIC_AUTH_USER" ] || [ -z "$BASIC_AUTH_PASS" ]; then
  echo "ERROR: basic-auth secrets empty — refusing to start unauthenticated." >&2
  exit 1
fi

exec ./.venv/bin/python -m uvicorn app:app --host 0.0.0.0 --port "$PORT"
