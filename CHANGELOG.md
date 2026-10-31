# Changelog

Preserve published release entries here, grouped by actual version and release date.
Do not invent prior releases or dates.

## [Unreleased]

### Fixed

- Prevent header controls from overlapping at mobile breakpoints and with long workspace/session labels; keep the composer button compact as text grows.

- Restore the browser-owned transcript after refresh, including stopped/error text; New chat removes it without changing model selection or agy session behavior.
- Recognize agy `finished_reason` in stream results so timeouts, interruption and errors cannot masquerade as successful complete answers.
- Persist conversation IDs received in terminal results, including streams ending without a newline.

### Changed

- Remove the visible header product name and replace Send/Stop text with accessible up-arrow/stop-square icons.

- Treat web prompts as literal text by disabling CLI slash-command and skill expansion.
- Document the agy 1.4.12 integration contract and add credential-free subprocess/SSE/OpenAI regression tests.
- Establish release-history ownership in `CHANGELOG.md`, current-state documentation, and pre-release/post-release ADR rules.
