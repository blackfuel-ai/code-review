import json
import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))

import format_summary
from format_summary import (
    format_duration,
    _extract_text_from_message,
    _extract_model_usage,
    _is_remote_provider_model,
    _unwrap_content_blocks,
    extract_review_text,
    load_cli_tokens,
    truncate,
    detect_language,
    extract_stats,
)


# ---------------------------------------------------------------------------
# format_duration
# ---------------------------------------------------------------------------

class TestFormatDuration:
    def test_zero_returns_empty(self):
        assert format_duration(0) == ""

    def test_negative_returns_empty(self):
        assert format_duration(-5) == ""

    def test_seconds_only(self):
        assert format_duration(30) == "30s"

    def test_one_second(self):
        assert format_duration(1) == "1s"

    def test_exactly_one_minute(self):
        assert format_duration(60) == "1m 0s"

    def test_minutes_and_seconds(self):
        assert format_duration(65) == "1m 5s"

    def test_multiple_minutes(self):
        assert format_duration(125) == "2m 5s"


# ---------------------------------------------------------------------------
# _extract_text_from_message
# ---------------------------------------------------------------------------

class TestExtractTextFromMessage:
    def test_stream_json_wrapped_message(self):
        message = {
            "type": "message",
            "content": [{"type": "text", "text": "Hello world"}],
        }
        assert _extract_text_from_message(message) == "Hello world"

    def test_direct_list_of_blocks(self):
        blocks = [{"type": "text", "text": "Direct block"}]
        assert _extract_text_from_message(blocks) == "Direct block"

    def test_multiple_text_blocks_joined(self):
        blocks = [
            {"type": "text", "text": "First part"},
            {"type": "text", "text": "Second part"},
        ]
        result = _extract_text_from_message(blocks)
        assert "First part" in result
        assert "Second part" in result

    def test_non_text_blocks_skipped(self):
        blocks = [
            {"type": "tool_use", "name": "Read"},
            {"type": "text", "text": "Only this"},
        ]
        assert _extract_text_from_message(blocks) == "Only this"

    def test_single_text_block_dict(self):
        block = {"type": "text", "text": "Plain text block"}
        assert _extract_text_from_message(block) == "Plain text block"

    def test_non_dict_non_list_returns_empty(self):
        assert _extract_text_from_message("raw string") == ""
        assert _extract_text_from_message(42) == ""
        assert _extract_text_from_message(None) == ""

    def test_empty_list_returns_empty(self):
        assert _extract_text_from_message([]) == ""

    def test_stream_json_with_tool_use_only_returns_empty(self):
        message = {
            "type": "message",
            "content": [{"type": "tool_use", "name": "Read"}],
        }
        assert _extract_text_from_message(message) == ""


# ---------------------------------------------------------------------------
# extract_review_text
# ---------------------------------------------------------------------------

class TestExtractReviewText:
    def test_empty_list_returns_empty(self):
        assert extract_review_text([]) == ""

    def test_assistant_messages_extracted(self):
        messages = [
            {
                "type": "assistant",
                "message": {
                    "type": "message",
                    "content": [{"type": "text", "text": "Review finding."}],
                },
            }
        ]
        assert "Review finding." in extract_review_text(messages)

    def test_result_message_included(self):
        messages = [
            {"type": "result", "result": "Final summary.", "num_turns": 1},
        ]
        assert "Final summary." in extract_review_text(messages)

    def test_assistant_and_result_aggregated(self):
        messages = [
            {
                "type": "assistant",
                "message": {
                    "type": "message",
                    "content": [{"type": "text", "text": "Intermediate text."}],
                },
            },
            {"type": "result", "result": "Final conclusion.", "num_turns": 2},
        ]
        text = extract_review_text(messages)
        assert "Intermediate text." in text
        assert "Final conclusion." in text

    def test_non_assistant_non_result_messages_skipped(self):
        messages = [
            {"type": "system", "message": "System prompt."},
            {"type": "user", "message": "User message."},
            {"type": "result", "result": "Only this.", "num_turns": 1},
        ]
        text = extract_review_text(messages)
        assert "System prompt." not in text
        assert "User message." not in text
        assert "Only this." in text

    def test_empty_result_field_skipped(self):
        messages = [
            {"type": "result", "result": "", "num_turns": 1},
        ]
        assert extract_review_text(messages) == ""


# ---------------------------------------------------------------------------
# truncate
# ---------------------------------------------------------------------------

class TestTruncate:
    def test_short_text_unchanged(self):
        text = "Short text"
        assert truncate(text, 100) == text

    def test_exact_limit_unchanged(self):
        text = "a" * 50
        assert truncate(text, 50) == text

    def test_long_text_truncated(self):
        text = "a" * 200
        result = truncate(text, 100)
        assert result.startswith("a" * 100)
        assert "truncated" in result
        assert "200 chars total" in result

    def test_truncated_includes_total_length(self):
        text = "x" * 1000
        result = truncate(text, 500)
        assert "1000 chars total" in result


# ---------------------------------------------------------------------------
# detect_language
# ---------------------------------------------------------------------------

class TestDetectLanguage:
    def test_json_object(self):
        assert detect_language('{"key": "value"}') == "json"

    def test_json_array(self):
        assert detect_language('[1, 2, 3]') == "json"

    def test_python_def(self):
        assert detect_language("def foo():\n    pass") == "python"

    def test_python_import(self):
        assert detect_language("import os\nfoo()") == "python"

    def test_xml(self):
        assert detect_language("<root><child/></root>") == "xml"

    def test_plain_text(self):
        assert detect_language("Just some plain text here.") == ""

    def test_javascript_function(self):
        assert detect_language("function foo() {}") == "javascript"

    def test_javascript_const(self):
        assert detect_language("const x = 1;") == "javascript"


# ---------------------------------------------------------------------------
# extract_stats
# ---------------------------------------------------------------------------

class TestExtractStats:
    def test_empty_messages(self):
        stats = extract_stats([])
        assert stats == {
            "tool_calls": 0, "turns": 0, "cost": 0.0,
            "input_tokens": 0, "output_tokens": 0,
        }

    def test_tool_use_count(self):
        messages = [
            {
                "type": "assistant",
                "message": {"type": "tool_use", "name": "Read"},
            },
            {
                "type": "assistant",
                "message": {"type": "tool_use", "name": "Bash"},
            },
        ]
        stats = extract_stats(messages)
        assert stats["tool_calls"] == 2

    def test_remote_provider_reads_token_file(self, monkeypatch, tmp_path):
        """Remote-provider models read real tokens from the CLI token file."""
        token_file = tmp_path / "tokens.json"
        token_file.write_text(json.dumps({
            "input_tokens": 93999,
            "output_tokens": 13855,
            "total_tokens": 107854,
            "total_cost": 1.256,
        }))
        monkeypatch.setattr(format_summary, "TOKENS_FILE", str(token_file))
        messages = [
            {
                "type": "result",
                "num_turns": 5,
                "modelUsage": {
                    "oai@MiniMaxAI/MiniMax-M2.7": {
                        "inputTokens": 500,
                        "outputTokens": 4387,
                        "costUSD": 0.069,
                    }
                },
            },
        ]
        stats = extract_stats(messages)
        assert stats["turns"] == 5
        assert stats["input_tokens"] == 93999
        assert stats["output_tokens"] == 13855
        assert abs(stats["cost"] - 1.256) < 1e-9

    def test_remote_provider_no_token_file(self, monkeypatch):
        """Remote-provider model without token file falls back to zeros."""
        monkeypatch.setattr(format_summary, "TOKENS_FILE", "/tmp/nonexistent.json")
        messages = [
            {
                "type": "result",
                "num_turns": 3,
            },
        ]
        stats = extract_stats(messages)
        assert stats["turns"] == 3
        assert stats["input_tokens"] == 0
        assert stats["output_tokens"] == 0
        assert stats["cost"] == 0.0

    def test_remote_provider_mixed_messages(self, monkeypatch, tmp_path):
        """Tool calls and turns counted; tokens from the CLI token file."""
        token_file = tmp_path / "tokens.json"
        token_file.write_text(json.dumps({
            "input_tokens": 28976,
            "output_tokens": 217,
            "total_cost": 0.0597,
        }))
        monkeypatch.setattr(format_summary, "TOKENS_FILE", str(token_file))
        messages = [
            {
                "type": "assistant",
                "message": {"type": "tool_use", "name": "Read"},
            },
            {"type": "user", "message": "tool result"},
            {
                "type": "result",
                "num_turns": 3,
            },
        ]
        stats = extract_stats(messages)
        assert stats["tool_calls"] == 1
        assert stats["turns"] == 3
        assert stats["input_tokens"] == 28976
        assert stats["output_tokens"] == 217

    def test_assistant_with_content_list(self):
        messages = [
            {
                "type": "assistant",
                "message": [
                    {"type": "tool_use", "name": "Read"},
                    {"type": "tool_use", "name": "Bash"},
                    {"type": "text", "text": "thinking"},
                ],
            },
        ]
        stats = extract_stats(messages)
        assert stats["tool_calls"] == 2

    def test_stream_json_envelope_unwrapped(self):
        """Stream-json wraps assistant messages in {"type": "message", "content": [...]}."""
        messages = [
            {
                "type": "assistant",
                "message": {
                    "type": "message",
                    "content": [
                        {"type": "tool_use", "name": "Read"},
                        {"type": "tool_use", "name": "Bash"},
                        {"type": "text", "text": "analysis"},
                    ],
                },
            },
        ]
        stats = extract_stats(messages)
        assert stats["tool_calls"] == 2

    def test_result_usage_without_model_usage_suppressed(self, monkeypatch):
        """Without modelUsage, result.usage tokens are not trusted for native models."""
        monkeypatch.setattr(format_summary, "MODEL", "claude-sonnet-4-6")
        messages = [
            {
                "type": "result",
                "num_turns": 2,
                "total_cost_usd": 0.01,
                "usage": {
                    "prompt_tokens": 3000,
                    "completion_tokens": 800,
                },
            },
        ]
        stats = extract_stats(messages)
        assert stats["input_tokens"] == 0
        assert stats["output_tokens"] == 0
        assert stats["cost"] == 0.0


# ---------------------------------------------------------------------------
# _unwrap_content_blocks
# ---------------------------------------------------------------------------

class TestUnwrapContentBlocks:
    def test_stream_json_envelope(self):
        message = {"type": "message", "content": [{"type": "text", "text": "hi"}]}
        blocks = _unwrap_content_blocks(message)
        assert len(blocks) == 1
        assert blocks[0]["type"] == "text"

    def test_bare_dict(self):
        message = {"type": "text", "text": "hello"}
        blocks = _unwrap_content_blocks(message)
        assert blocks == [message]

    def test_plain_list(self):
        blocks_in = [{"type": "tool_use", "name": "Read"}]
        assert _unwrap_content_blocks(blocks_in) == blocks_in

    def test_non_dict_non_list(self):
        assert _unwrap_content_blocks("string") == []
        assert _unwrap_content_blocks(None) == []
        assert _unwrap_content_blocks(42) == []

    def test_envelope_with_empty_content(self):
        message = {"type": "message", "content": []}
        assert _unwrap_content_blocks(message) == []


# ---------------------------------------------------------------------------
# _extract_model_usage
# ---------------------------------------------------------------------------

class TestExtractModelUsage:
    def test_single_model(self):
        model_usage = {
            "oai@MiniMaxAI/MiniMax-M2.7": {
                "inputTokens": 1300,
                "outputTokens": 5032,
                "cacheReadInputTokens": 0,
                "cacheCreationInputTokens": 0,
                "costUSD": 0.07938,
            }
        }
        in_tok, out_tok, cost = _extract_model_usage(model_usage)
        assert in_tok == 1300
        assert out_tok == 5032
        assert abs(cost - 0.07938) < 1e-9

    def test_with_cache_tokens(self):
        model_usage = {
            "model-a": {
                "inputTokens": 1000,
                "outputTokens": 500,
                "cacheReadInputTokens": 200,
                "cacheCreationInputTokens": 100,
                "costUSD": 0.05,
            }
        }
        in_tok, out_tok, cost = _extract_model_usage(model_usage)
        assert in_tok == 1300
        assert out_tok == 500

    def test_multiple_models_summed(self):
        model_usage = {
            "model-a": {"inputTokens": 1000, "outputTokens": 500, "costUSD": 0.03},
            "model-b": {"inputTokens": 2000, "outputTokens": 800, "costUSD": 0.05},
        }
        in_tok, out_tok, cost = _extract_model_usage(model_usage)
        assert in_tok == 3000
        assert out_tok == 1300
        assert abs(cost - 0.08) < 1e-9

    def test_empty_dict(self):
        assert _extract_model_usage({}) == (0, 0, 0.0)

    def test_non_dict_entry_skipped(self):
        model_usage = {"model-a": "invalid"}
        assert _extract_model_usage(model_usage) == (0, 0, 0.0)


# ---------------------------------------------------------------------------
# extract_stats — modelUsage preference
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# _is_remote_provider_model
# ---------------------------------------------------------------------------

class TestIsRemoteProviderModel:
    def test_oai_prefix(self):
        assert _is_remote_provider_model("oai@MiniMaxAI/MiniMax-M2.7") is True

    def test_g_prefix(self):
        assert _is_remote_provider_model("g@gemini-2.5-pro") is True

    def test_native_claude(self):
        assert _is_remote_provider_model("claude-sonnet-4-6") is False

    def test_empty_string(self):
        assert _is_remote_provider_model("") is False


# ---------------------------------------------------------------------------
# extract_stats — modelUsage preference
# ---------------------------------------------------------------------------

class TestExtractStatsModelUsage:
    def test_model_usage_preferred_over_usage(self, monkeypatch):
        """modelUsage has cumulative totals; usage is last-turn only.

        Requires a non-remote-provider MODEL so tokens are not suppressed.
        """
        monkeypatch.setattr(format_summary, "MODEL", "claude-sonnet-4-6")
        messages = [
            {
                "type": "result",
                "num_turns": 5,
                "total_cost_usd": 0.01,
                "usage": {
                    "input_tokens": 200,
                    "output_tokens": 1229,
                },
                "modelUsage": {
                    "claude-sonnet-4-6": {
                        "inputTokens": 1300,
                        "outputTokens": 5032,
                        "cacheReadInputTokens": 0,
                        "cacheCreationInputTokens": 0,
                        "costUSD": 0.07938,
                    }
                },
            },
        ]
        stats = extract_stats(messages)
        assert stats["input_tokens"] == 1300
        assert stats["output_tokens"] == 5032
        assert abs(stats["cost"] - 0.07938) < 1e-9

    def test_no_model_usage_returns_zeros(self, monkeypatch):
        """Without modelUsage, tokens/cost are suppressed even for native models."""
        monkeypatch.setattr(format_summary, "MODEL", "claude-sonnet-4-6")
        messages = [
            {
                "type": "result",
                "num_turns": 2,
                "usage": {"input_tokens": 800, "output_tokens": 400},
            },
        ]
        stats = extract_stats(messages)
        assert stats["input_tokens"] == 0
        assert stats["output_tokens"] == 0
        assert stats["cost"] == 0.0
