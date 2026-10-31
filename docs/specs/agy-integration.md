# agy integration

## Contract

The backend runs one `agy --print` subprocess per request, with explicit
`--print-timeout` and the existing server deadline. A zero `AGY_TIMEOUT` means
no deadline. Conversation continuity uses `--conversation`, not a long-lived
stdin process. Dynamic prompts stay attached to `-p=` and are literal text:
`--disable-slash-commands` prevents user content from expanding CLI commands or
skills. Existing workspace, model, sandbox, MCP filtering and permission
configuration remain unchanged.

For `stream-json`, normalize NDJSON into the existing SSE events. Preserve
terminal response, usage, conversation ID and optional `finished_reason`.
Any nonempty `finished_reason` means an incomplete turn (including
`print_timeout`, `interrupted`, and `error`), even if `status` says `SUCCESS`.
Emit an error after the result so the browser retains partial text but does not
mark it complete; the OpenAI-compatible endpoint must fail rather than return
`finish_reason: stop` for a partial answer. Runs without the optional field keep
the existing success behavior. Persist IDs from either init or result events
when persistence is enabled, including a result at EOF without a newline.

`agy models` is tab-separated stdout; startup progress belongs to stderr. Keep
model IDs and names as supplied by the CLI, without downgrading selections or
assuming unsupported JSON flags.

## Verification

Run `python -m unittest discover -s tests -v` and `python -m unittest test_app -v`
in an environment with the application's existing FastAPI dependencies. Set
`AGY_SCRATCH` and `AGY_CONVERSATIONS_FILE` outside the checkout before importing
`app`. Tests use synthetic CLI events and no credentials or model calls.

For release E2E, verify a real model list, a streamed response, continuation
through refresh, stop, and a deliberate timeout in the browser. A normal reply
must end ready; an incomplete reply must retain its partial content and show an
error. Browser checks require the deployment's normal authentication; do not
capture credentials in test artifacts.

## Compatibility basis

The last functional application commit is `1323565` (2026-09-25). It does not
pin or record an installed agy version; an exact historical version cannot be
established from Git. Official release `1.2.11` was published that morning and
is a date-based comparison boundary, not proof of what was installed.

Current CLI `1.4.12` supports the invocation flags above. The official
[1.4.11 notes](https://github.com/google-antigravity/antigravity-cli/releases/tag/1.4.11)
define `finished_reason`; this integration consumes it to distinguish partial
answers. [1.4.7](https://github.com/google-antigravity/antigravity-cli/releases/tag/1.4.7)
reduces headless CPU without application changes, and
[1.2.16](https://github.com/google-antigravity/antigravity-cli/releases/tag/1.2.16)
fixes premature completion around background tasks. Terminal rendering and
clipboard changes do not benefit this web UI. The
[official release feed](https://github.com/google-antigravity/antigravity-cli/releases)
is the release-history authority; bundled `agy changelog` can lag the binary.
