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

## Browser history

The browser saves its current transcript in localStorage after each completed,
stopped or failed request and restores it synchronously on refresh. Restore only
when both browser user ID and active conversation ID match (including a URL `cid`);
this is browser-owned display history, not a server history API. New chat removes
the snapshot, including when cancelling an active response. Keep message content,
tool/usage badges and error/cancellation text; do not change model selection or
session continuation. Malformed or unavailable storage must not break the UI.

## Header and composer UI

The header keeps its logo/status and workspace, session, model and New chat controls,
but no visible product-name text. Preserve the browser document title and empty-state
copy. Header actions wrap within their available width; long workspace/session labels
truncate without covering another control.

`#send` is a fixed-size up-arrow icon button, labelled and titled `Send`. During a
request the same button shows a stop square, labelled and titled `Stop`, and cancels
the active response. Keep Enter/Shift+Enter behavior and keyboard focus indicators.
The textarea shrinks within the composer; growing text must not stretch the icon.

Verify header/composer geometry at 320, 360, 390, 768 and 1280 CSS pixels and at
419/420/421 and 639/640/641 around both existing mobile breakpoints. Include long
workspace, conversation ID and multiline composer fixtures. Require no page horizontal
overflow, overlapping or covered controls, or controls outside the viewport.
`tests/test_ui_geometry.cjs` exports the authenticated real-browser assertions; use
the normal isolated-browser E2E flow, including actual send/stop and session reload.
Label local HTML response substitution as candidate QA, not deployed verification.

## Verification

Run `python -m unittest discover -s tests -v` and `python -m unittest test_app -v`
in an environment with the application's existing FastAPI dependencies. Set
`AGY_SCRATCH` and `AGY_CONVERSATIONS_FILE` outside the checkout before importing
`app`. Tests use synthetic CLI events and no credentials or model calls. Run
`node --test tests/test_browser_history.cjs` for browser-state regressions using
Node's built-in VM and DOM stubs; no frontend dependencies are required.

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
