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
