#!/usr/bin/env python3
"""Format bf-review results as a Job Summary and sticky PR comment.

Reads structured JSON messages from run_review.py and produces a rich
execution trace in the GitHub Actions Job Summary.

Required env:
    GH_TOKEN            GitHub token for API calls
    GITHUB_REPOSITORY   owner/repo
    GITHUB_RUN_ID       Actions run ID
    GITHUB_STEP_SUMMARY Path to step summary file
    PR_NUMBER           Pull request number
    PR_URL              Pull request HTML URL
    TRIGGER_USER        PR author login
    REVIEW_OUTCOME      "success" or "failure"
    REVIEW_DURATION     Duration in seconds (may be empty)

Optional env:
    BF_REVIEW_MODEL     Model identifier for the summary table
"""

import json
import os
import pathlib
import sys

# bf_review_trace is bundled next to this script.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from bf_review_trace import (  # noqa: E402
    _extract_model_usage,
    _is_remote_provider_model,
    _unwrap_content_blocks,
    append_job_summary,
    build_job_summary as _build_job_summary,
    detect_language,
    extract_stats as _extract_stats_with_paths,
    format_duration,
    load_cli_tokens as _load_cli_tokens_from_path,
    truncate,
)

MODEL = os.environ.get("BF_REVIEW_MODEL", "oai@MiniMaxAI/MiniMax-M2.7")
MESSAGES_FILE = "/tmp/claude-review-messages.json"
OUTPUT_FILE = "/tmp/claude-review-output.txt"
TOKENS_FILE = "/tmp/claude-review-tokens.json"


# ---------------------------------------------------------------------------
# Module-level wrappers (resolve MODEL/TOKENS_FILE at call time so tests can
# monkeypatch them).
# ---------------------------------------------------------------------------


def load_cli_tokens() -> dict:
    return _load_cli_tokens_from_path(TOKENS_FILE)


def extract_stats(messages: list) -> dict:
    return _extract_stats_with_paths(messages, model=MODEL, tokens_path=TOKENS_FILE)


# ---------------------------------------------------------------------------
# Outcome / IO helpers
# ---------------------------------------------------------------------------


def get_env(name: str, default: str = "") -> str:
    return os.environ.get(name, default)


def determine_outcome() -> tuple:
    """Returns (failed: bool, duration_seconds: int)."""
    review_outcome = get_env("REVIEW_OUTCOME")
    review_duration = get_env("REVIEW_DURATION")
    duration = int(review_duration) if review_duration.isdigit() else 0
    return (review_outcome != "success"), duration


def load_messages() -> list:
    try:
        with open(MESSAGES_FILE) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return []


# ---------------------------------------------------------------------------
# Review-specific text extraction (used by tests + sticky comment posting)
# ---------------------------------------------------------------------------


def _extract_text_from_message(message) -> str:
    """Extract text from an assistant message (dict or list of content blocks).

    Stream-json wraps content in ``{"type": "message", "content": [...]}``.
    Also handles bare content blocks (``{"type": "text", ...}``) and plain lists.
    """
    if not isinstance(message, (dict, list)):
        return ""

    if isinstance(message, dict) and message.get("type") == "message":
        message = message.get("content", [])

    if isinstance(message, dict) and message.get("type") == "text":
        return (message.get("text") or "").strip()

    if isinstance(message, list):
        parts = []
        for block in message:
            if isinstance(block, dict) and block.get("type") == "text":
                text = (block.get("text") or "").strip()
                if text:
                    parts.append(text)
        return "\n\n".join(parts)

    return ""


def extract_review_text(messages: list) -> str:
    """Extract assistant text and final result from captured messages."""
    parts = []
    for msg in messages:
        msg_type = msg.get("type", "")
        if msg_type == "assistant":
            text = _extract_text_from_message(msg.get("message"))
            if text:
                parts.append(text)
        elif msg_type == "result":
            result_text = (msg.get("result") or "").strip()
            if result_text:
                parts.append(result_text)
    return "\n\n".join(parts)


# ---------------------------------------------------------------------------
# Header + main
# ---------------------------------------------------------------------------


def build_header(failed: bool, duration: int) -> str:
    repo = get_env("GITHUB_REPOSITORY")
    run_id = get_env("GITHUB_RUN_ID")
    trigger_user = get_env("TRIGGER_USER")
    job_url = f"https://github.com/{repo}/actions/runs/{run_id}"
    duration_str = format_duration(duration)
    status_icon = "❌" if failed else "✅"

    if failed:
        header = f"{status_icon} **Claude encountered an error"
        if duration_str:
            header += f" after {duration_str}"
        header += f"** —— [View job]({job_url})"
    else:
        header = f"{status_icon} **Claude finished @{trigger_user}'s task"
        if duration_str:
            header += f" in {duration_str}"
        header += f"** —— [View job]({job_url})"

    return header


def main() -> None:
    failed, duration = determine_outcome()
    messages = load_messages()

    pr_number = get_env("PR_NUMBER")
    pr_url = get_env("PR_URL")
    trigger_user = get_env("TRIGGER_USER")

    extra_rows: list[tuple[str, str]] = []
    if pr_number:
        pr_label = f"[#{pr_number}]({pr_url})" if pr_url else f"#{pr_number}"
        extra_rows.append(("PR", pr_label))
    if trigger_user:
        extra_rows.append(("Trigger", f"@{trigger_user}"))

    summary = _build_job_summary(
        title="Claude Code Report",
        header=build_header(failed, duration),
        messages=messages,
        model=MODEL,
        duration=duration,
        tokens_path=TOKENS_FILE,
        output_path=OUTPUT_FILE,
        extra_metadata_rows=extra_rows,
    )
    append_job_summary(summary)

    print(
        f"Summary written: {'failed' if failed else 'success'}, "
        f"{len(messages)} messages",
        file=sys.stderr,
    )


# Re-export shared helpers so existing tests can import them from this module.
__all__ = [
    "MESSAGES_FILE",
    "MODEL",
    "OUTPUT_FILE",
    "TOKENS_FILE",
    "_extract_model_usage",
    "_extract_text_from_message",
    "_is_remote_provider_model",
    "_unwrap_content_blocks",
    "build_header",
    "determine_outcome",
    "detect_language",
    "extract_review_text",
    "extract_stats",
    "format_duration",
    "load_cli_tokens",
    "load_messages",
    "main",
    "truncate",
]


if __name__ == "__main__":
    main()
