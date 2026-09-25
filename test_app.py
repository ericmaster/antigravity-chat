import asyncio
import unittest
from unittest.mock import patch

import app


class BuildArgvTests(unittest.TestCase):
    def test_prompt_is_attached_to_print_flag(self):
        argv = app._build_argv(
            "default",
            prompt="--dangerously-skip-permissions is not the prompt",
            target_cwd="/tmp/workspace",
        )

        prompt_args = [arg for arg in argv if arg.startswith("-p=")]
        self.assertEqual(prompt_args, ["-p=--dangerously-skip-permissions is not the prompt"])
        self.assertIn("--dangerously-skip-permissions", argv)
        self.assertNotIn("-p", argv)


class StreamParsingTests(unittest.TestCase):
    def test_parses_documented_tool_step(self):
        events = app._parse_ndjson_line(
            '{"event":"step_update","step_update":{"step_type":"tool",'
            '"tool_name":"run_command","tool_info":{"name":"run_command",'
            '"parameters":{"CommandLine":"pwd"}},"state":"DONE"}}'
        )

        self.assertEqual(events, [{
            "event": "tool",
            "tool": "run_command",
            "args": {"CommandLine": "pwd"},
            "status": "DONE",
        }])

    def test_preserves_final_response_and_usage(self):
        events = app._parse_ndjson_line(
            '{"event":"result","result":{"conversation_id":"cid-1",'
            '"status":"SUCCESS","response":"Hello","usage":{"total_tokens":3}}}'
        )

        self.assertEqual(events, [{
            "event": "result",
            "usage": {"total_tokens": 3},
            "status": "SUCCESS",
            "response": "Hello",
            "conversation_id": "cid-1",
        }])


class PromptResolutionTests(unittest.TestCase):
    def test_resumed_conversation_sends_only_latest_message(self):
        request = app.ChatRequest(
            messages=[
                app.Message(role="user", content="Earlier question"),
                app.Message(role="assistant", content="Earlier answer"),
                app.Message(role="user", content="Latest question"),
            ],
            conversation_id="conversation-1",
        )

        with patch.object(app, "AGY_PERSIST_CONVERSATIONS", True):
            prompt, conversation_id, continue_last, _ = asyncio.run(
                app._resolve_prompt_and_session(request)
            )

        self.assertEqual(prompt, "Latest question")
        self.assertEqual(conversation_id, "conversation-1")
        self.assertFalse(continue_last)


if __name__ == "__main__":
    unittest.main()
