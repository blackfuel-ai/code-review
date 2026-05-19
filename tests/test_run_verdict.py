import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))

from run_verdict import (
    detect_verdict,
    build_verdict_prompt,
    extract_token_usage,
    _extract_model_usage,
    SENTINEL_APPROVED,
    SENTINEL_CHANGES,
)


# ---------------------------------------------------------------------------
# detect_verdict
# ---------------------------------------------------------------------------


class TestDetectVerdict:
    def test_approved_sentinel(self):
        assert detect_verdict(f"All good.\n\n{SENTINEL_APPROVED}") == "approved"

    def test_changes_requested_sentinel(self):
        assert detect_verdict(f"Issues found.\n\n{SENTINEL_CHANGES}") == "changes_requested"

    def test_no_sentinel_returns_none(self):
        assert detect_verdict("Some text without verdict.") is None

    def test_empty_string_returns_none(self):
        assert detect_verdict("") is None

    def test_changes_requested_takes_priority(self):
        text = f"{SENTINEL_APPROVED}\n{SENTINEL_CHANGES}"
        assert detect_verdict(text) == "changes_requested"

    def test_sentinel_with_bold_markdown(self):
        assert detect_verdict(f"**{SENTINEL_APPROVED}**") == "approved"

    def test_sentinel_with_underscore_markdown(self):
        assert detect_verdict(f"__{SENTINEL_CHANGES}__") == "changes_requested"

    def test_sentinel_on_same_line_as_other_text(self):
        assert detect_verdict(f"Rationale here. {SENTINEL_APPROVED}") == "approved"

    def test_last_sentinel_wins(self):
        # Rationale mentions CHANGES_REQUESTED but final verdict is APPROVED
        text = f"Earlier the review said {SENTINEL_CHANGES} but after triage:\n{SENTINEL_APPROVED}"
        assert detect_verdict(text) == "approved"

    def test_last_sentinel_wins_reverse(self):
        # Earlier APPROVED overridden by final CHANGES_REQUESTED
        text = f"Initially {SENTINEL_APPROVED}\nBut on reflection:\n{SENTINEL_CHANGES}"
        assert detect_verdict(text) == "changes_requested"

    def test_skips_blank_lines_at_end(self):
        text = f"Rationale.\n{SENTINEL_APPROVED}\n\n\n"
        assert detect_verdict(text) == "approved"


# ---------------------------------------------------------------------------
# build_verdict_prompt
# ---------------------------------------------------------------------------


class TestBuildVerdictPrompt:
    def test_includes_review_content(self):
        prompt = build_verdict_prompt("Some code review findings.")
        assert "Some code review findings." in prompt

    def test_includes_sentinel_instructions(self):
        prompt = build_verdict_prompt("Findings.")
        assert SENTINEL_APPROVED in prompt
        assert SENTINEL_CHANGES in prompt

    def test_includes_severity_criteria(self):
        prompt = build_verdict_prompt("Findings.")
        assert "risk to production" in prompt
        assert "security" in prompt
        assert "pre-existing" in prompt

    def test_does_not_include_reviewer_notes_section(self):
        # Reviewer notes are fed into run_review.py, NEVER into the verdict
        # prompt. The verdict only sees the review output.
        prompt = build_verdict_prompt("Findings.")
        assert "Human Reviewer Replies" not in prompt
        assert "reviewer_notes" not in prompt

    def test_warns_against_prompt_injection_from_comment_bodies(self):
        # The verdict prompt must explicitly reject attempts by comment
        # bodies to override findings — this is a defence against people
        # pasting "treat as authoritative" text into review output.
        prompt = build_verdict_prompt("Findings.")
        assert "authoritative" in prompt.lower()


# ---------------------------------------------------------------------------
# extract_token_usage
# ---------------------------------------------------------------------------


class TestExtractTokenUsage:
    def test_empty_messages(self):
        result = extract_token_usage([])
        assert result == {"input_tokens": 0, "output_tokens": 0}

    def test_anthropic_fields(self):
        messages = [
            {
                "type": "result",
                "usage": {
                    "input_tokens": 1000,
                    "cache_creation_input_tokens": 200,
                    "cache_read_input_tokens": 300,
                    "output_tokens": 500,
                },
            },
        ]
        result = extract_token_usage(messages)
        assert result["input_tokens"] == 1500
        assert result["output_tokens"] == 500

    def test_openai_fields(self):
        messages = [
            {
                "type": "result",
                "usage": {
                    "prompt_tokens": 4000,
                    "completion_tokens": 600,
                    "total_tokens": 4600,
                },
            },
        ]
        result = extract_token_usage(messages)
        assert result["input_tokens"] == 4000
        assert result["output_tokens"] == 600

    def test_anthropic_preferred_over_openai(self):
        messages = [
            {
                "type": "result",
                "usage": {
                    "input_tokens": 1000,
                    "output_tokens": 500,
                    "prompt_tokens": 9999,
                    "completion_tokens": 9999,
                },
            },
        ]
        result = extract_token_usage(messages)
        assert result["input_tokens"] == 1000
        assert result["output_tokens"] == 500

    def test_no_usage_in_result(self):
        messages = [{"type": "result", "result": "done"}]
        result = extract_token_usage(messages)
        assert result == {"input_tokens": 0, "output_tokens": 0}

    def test_ignores_non_result_messages(self):
        messages = [
            {"type": "assistant", "message": {"type": "text", "text": "hi"}},
            {"type": "result", "usage": {"input_tokens": 100, "output_tokens": 50}},
        ]
        result = extract_token_usage(messages)
        assert result["input_tokens"] == 100
        assert result["output_tokens"] == 50

    def test_model_usage_preferred_over_usage(self):
        messages = [
            {
                "type": "result",
                "usage": {"input_tokens": 200, "output_tokens": 1229},
                "modelUsage": {
                    "oai@MiniMaxAI/MiniMax-M2.7": {
                        "inputTokens": 1300,
                        "outputTokens": 5032,
                        "cacheReadInputTokens": 0,
                        "cacheCreationInputTokens": 0,
                        "costUSD": 0.07938,
                    }
                },
            },
        ]
        result = extract_token_usage(messages)
        assert result["input_tokens"] == 1300
        assert result["output_tokens"] == 5032

    def test_falls_back_to_usage_without_model_usage(self):
        messages = [
            {"type": "result", "usage": {"input_tokens": 500, "output_tokens": 200}},
        ]
        result = extract_token_usage(messages)
        assert result["input_tokens"] == 500
        assert result["output_tokens"] == 200


# ---------------------------------------------------------------------------
# _extract_model_usage
# ---------------------------------------------------------------------------


class TestExtractModelUsage:
    def test_single_model(self):
        model_usage = {
            "oai@MiniMaxAI/MiniMax-M2.7": {
                "inputTokens": 1300,
                "outputTokens": 5032,
                "costUSD": 0.07938,
            }
        }
        in_tok, out_tok, cost = _extract_model_usage(model_usage)
        assert in_tok == 1300
        assert out_tok == 5032

    def test_empty(self):
        assert _extract_model_usage({}) == (0, 0, 0.0)
