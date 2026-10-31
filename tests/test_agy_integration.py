"""Credential-free integration regressions. Spec: docs/specs/agy-integration.md."""

import asyncio
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from fastapi import HTTPException

import app


class InvocationTests(unittest.TestCase):
    # Spec: docs/specs/agy-integration.md
    def test_literal_prompt_preserves_existing_options(self):
        # Spec: via InvocationTests
        with patch.object(app, "AGY_SANDBOX", True), patch.object(app, "AGY_TIMEOUT", 0):
            argv = app._build_argv("gemini-4-argon-high", prompt="/skill\n--mode auto",
                                   conversation_id="cid-1", target_cwd="/safe/workspace")
        self.assertIn("--disable-slash-commands", argv)
        self.assertIn("--dangerously-skip-permissions", argv)
        self.assertIn("--sandbox", argv)
        self.assertEqual(argv[argv.index("--model") + 1], "gemini-4-argon-high")
        self.assertEqual(argv[argv.index("--conversation") + 1], "cid-1")
        self.assertEqual(argv[argv.index("--add-dir") + 1], "/safe/workspace")
        self.assertEqual(argv[argv.index("--print-timeout") + 1], "0s")
        self.assertEqual(argv[-1], "-p=/skill\n--mode auto")
        self.assertNotIn("--input-format", argv)
        self.assertNotIn("--settings", argv)


class ResultTests(unittest.TestCase):
    # Spec: docs/specs/agy-integration.md
    def test_early_finish_is_not_success_even_with_success_status(self):
        # Spec: via ResultTests
        for reason in ("print_timeout", "interrupted", "error", "future_reason"):
            for nested in (True, False):
                with self.subTest(reason=reason, nested=nested):
                    result = {"status": "SUCCESS", "response": "Partial", "finished_reason": reason}
                    payload = {"event": "result", "result": result} if nested else {"event": "result", **result}
                    events = app._parse_ndjson_line(json.dumps(payload))
                    self.assertEqual(events[0]["finished_reason"], reason)
                    self.assertEqual(events[1]["event"], "error")
                    self.assertIn(reason, events[1]["error"])

    def test_explicit_error_takes_precedence(self):
        # Spec: via ResultTests
        events = app._parse_ndjson_line(json.dumps({"event": "result", "result": {
            "status": "ERROR", "finished_reason": "error", "error": "Hook rejected prompt"}}))
        self.assertEqual(events[-1], {"event": "error", "error": "Hook rejected prompt"})

    def test_normal_result_remains_successful(self):
        # Spec: via ResultTests
        events = app._parse_ndjson_line(json.dumps({"event": "result", "result": {
            "status": "SUCCESS", "response": "Complete", "usage": {"total_tokens": 9}}}))
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["response"], "Complete")
        self.assertNotIn("finished_reason", events[0])


class StreamTests(unittest.IsolatedAsyncioTestCase):
    # Spec: docs/specs/agy-integration.md
    async def asyncSetUp(self):
        # Spec: via StreamTests
        self.temp = tempfile.TemporaryDirectory(prefix="agy-integration-")
        self.addCleanup(self.temp.cleanup)
        self.patches = [
            patch.object(app, "SCRATCH", Path(self.temp.name)),
            patch.object(app, "CONVERSATIONS_FILE", Path(self.temp.name) / "conversations.json"),
            patch.object(app, "AGY_OUTPUT_FORMAT", "stream-json"),
            patch.object(app, "AGY_PERSIST_CONVERSATIONS", True),
            patch.object(app, "AGY_TIMEOUT", 5),
            patch.object(app, "_agy_environment", return_value={}),
        ]
        for item in self.patches:
            item.start()
            self.addCleanup(item.stop)

    def subprocess(self, payload, newline=True):
        # Spec: via StreamTests
        # A real OS subprocess exercises pipe decoding, EOF and reaping, not agy auth.
        code = "import sys; sys.stdout.buffer.write(" + repr(
            (json.dumps(payload, ensure_ascii=False) + ("\n" if newline else "")).encode()
        ) + "); sys.stdout.flush()"
        return patch.object(app, "_build_argv", return_value=[sys.executable, "-c", code])

    async def test_result_only_id_persists_with_and_without_final_newline(self):
        # Spec: via StreamTests
        for newline in (True, False):
            with self.subTest(newline=newline), self.subprocess({"event": "result", "result": {
                "status": "SUCCESS", "response": "Hola 🌍", "conversation_id": "cid-result"}}, newline):
                events = [event async for event in app._stream_agy("Hi", None, user_id="result-user")]
                self.assertEqual(events[0]["response"], "Hola 🌍")
                self.assertEqual(app.get_conversation_id("result-user"), "cid-result")

    async def test_disabled_persistence_does_not_write_result_id(self):
        # Spec: via StreamTests
        with patch.object(app, "AGY_PERSIST_CONVERSATIONS", False), self.subprocess({
            "event": "result", "result": {"status": "SUCCESS", "conversation_id": "cid-no-save"}}):
            _ = [event async for event in app._stream_agy("Hi", None)]
        self.assertFalse(app.CONVERSATIONS_FILE.exists())

    async def test_sse_preserves_partial_response_then_error_then_done(self):
        # Spec: via StreamTests
        request = app.ChatRequest(messages=[app.Message(role="user", content="Hi")])
        with self.subprocess({"event": "result", "result": {"status": "SUCCESS",
                             "response": "Partial", "finished_reason": "print_timeout"}}):
            response = await app.chat(request)
            blocks = [block async for block in response.body_iterator]
        events = [json.loads((block if isinstance(block, str) else bytes(block).decode()).removeprefix("data: "))
                  for block in blocks]
        self.assertEqual([e["event"] for e in events], ["start", "result", "error", "done"])
        self.assertEqual(events[1]["response"], "Partial")

    async def test_openai_rejects_partial_instead_of_finish_reason_stop(self):
        # Spec: via StreamTests
        request = app.ChatRequest(messages=[app.Message(role="user", content="Hi")])
        with self.subprocess({"event": "result", "result": {"status": "SUCCESS",
                             "response": "Partial", "finished_reason": "interrupted"}}):
            with self.assertRaises(HTTPException) as caught:
                await app.openai_chat(request)
        self.assertEqual(caught.exception.status_code, 500)
        self.assertIn("interrupted", caught.exception.detail)

    async def test_openai_success_keeps_complete_response(self):
        # Spec: via StreamTests
        request = app.ChatRequest(messages=[app.Message(role="user", content="Hi")])
        with self.subprocess({"event": "result", "result": {"status": "SUCCESS", "response": "Complete"}}):
            response = await app.openai_chat(request)
        choice = json.loads(bytes(response.body))["choices"][0]
        self.assertEqual(choice["message"]["content"], "Complete")
        self.assertEqual(choice["finish_reason"], "stop")

    async def test_server_deadline_still_bounds_subprocess(self):
        # Spec: via StreamTests
        with patch.object(app, "AGY_TIMEOUT", 0.05), patch.object(app, "_build_argv", return_value=[
            sys.executable, "-c", "import time; time.sleep(10)"]):
            events = await asyncio.wait_for(self.collect(), timeout=2)
        self.assertEqual(events[-1]["event"], "error")
        self.assertIn("timed out", events[-1]["error"])

    async def collect(self):
        # Spec: via StreamTests
        return [event async for event in app._stream_agy("Hi", None)]
