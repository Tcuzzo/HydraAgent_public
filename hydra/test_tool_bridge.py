#!/usr/bin/env python3
import unittest

from hydra.tool_bridge import (
    ToolCall,
    extract_bridged_tool_call,
    extract_narrated_tool_calls,
    is_supported_tool,
    looks_like_bare_json_output,
    parse_function_prefixed_tool_call,
    parse_tool_json,
    scrub_public_fake_output_preview,
    should_bridge_output,
    should_run_qwen_tool_bridge,
)


class TestToolBridge(unittest.TestCase):
    def test_function_prefixed_tool_call(self):
        call = parse_function_prefixed_tool_call(
            'function: read path="/tmp/file.txt" mode="r\\n"'
        )
        self.assertEqual(
            call,
            ToolCall(
                name="read",
                arguments={"path": "/tmp/file.txt", "mode": "r\n"},
                arguments_raw='{"mode": "r\\n", "path": "/tmp/file.txt"}',
            ),
        )

    def test_bare_json_tool_call(self):
        call = extract_bridged_tool_call('{"name":"bash","arguments":{"command":"pwd"}}')
        self.assertIsNotNone(call)
        self.assertEqual(call.name, "bash")
        self.assertEqual(call.arguments["command"], "pwd")

    def test_tool_field_alias(self):
        call = extract_bridged_tool_call('{"tool":"read","arguments":{"path":"/tmp/x"}}')
        self.assertIsNotNone(call)
        self.assertEqual(call.name, "read")

    def test_function_object_wrapper(self):
        call = parse_tool_json('{"function":{"write":{"path":"/foo","content":"x"}}}')
        self.assertEqual(
            call,
            ToolCall(
                name="write",
                arguments={"path": "/foo", "content": "x"},
                arguments_raw='{"content": "x", "path": "/foo"}',
            ),
        )

    def test_unsupported_tool_refused(self):
        self.assertIsNone(extract_bridged_tool_call('{"name":"web_search","arguments":{"q":"x"}}'))
        self.assertFalse(is_supported_tool("web_search"))

    def test_should_bridge_text_tool_output(self):
        self.assertTrue(should_bridge_output('function: exec command="git status --short"'))
        self.assertTrue(should_bridge_output('{"name":"read","arguments":{"path":"README.md"}}'))
        self.assertFalse(should_bridge_output('function: curl url="https://x"'))

    def test_should_run_qwen_tool_bridge_head_shape(self):
        self.assertTrue(
            should_run_qwen_tool_bridge(
                {"output": '```json\n{"tool":"apply_patch","patch":"*** Begin Patch"}\n```'}
            )
        )
        self.assertFalse(should_run_qwen_tool_bridge({"output": "HYDRA_SUPPRESSED"}))

    def test_extract_narrated_tool_calls_mixed_formats(self):
        text = """
        thinking...
        {"name":"read","arguments":{"path":"/alpha"}}
        ```json
        {"tool":"bash","command":"pwd"}
        ```
        function: write path=/tmp/out.txt content="hello world"
        """
        calls = extract_narrated_tool_calls(text)
        self.assertEqual(
            calls,
            [
                ToolCall(
                    name="read",
                    arguments={"path": "/alpha"},
                    arguments_raw='{"path": "/alpha"}',
                ),
                ToolCall(
                    name="bash",
                    arguments={"command": "pwd"},
                    arguments_raw='{"command": "pwd"}',
                ),
                ToolCall(
                    name="write",
                    arguments={"path": "/tmp/out.txt", "content": "hello world"},
                    arguments_raw='{"content": "hello world", "path": "/tmp/out.txt"}',
                ),
            ],
        )

    def test_scrub_fake_output_preview(self):
        self.assertEqual(
            scrub_public_fake_output_preview('tool: read path=/tmp/nope'),
            "HYDRA_SUPPRESSED_PENDING_TOOL_JSON",
        )
        self.assertEqual(
            scrub_public_fake_output_preview('{"name":'),
            "HYDRA_SUPPRESSED_PENDING_TOOL_JSON",
        )
        self.assertEqual(
            scrub_public_fake_output_preview('{"name":"read","arguments":{"path":"ok"}}'),
            '{"name":"read","arguments":{"path":"ok"}}',
        )

    def test_bare_json_detection(self):
        self.assertTrue(looks_like_bare_json_output('{"name":"read","arguments":{}}'))
        self.assertFalse(looks_like_bare_json_output("not json"))


if __name__ == "__main__":
    unittest.main()
