"""bf-review CLI runner + Job Summary formatter.

Captures the review CLI's stream-json output into messages/tokens/raw files,
then renders a GitHub Actions Job Summary with a metadata table, a
collapsible execution trace (one block per tool_use + tool_result), and a
raw log tail.
"""

from __future__ import annotations

import json
import os
import pathlib
import re
import subprocess
import sys
import time

DEFAULT_MAX_RESULT_CHARS = 2000
DEFAULT_MAX_PARAM_CHARS = 500


# ---------------------------------------------------------------------------
# Command building
# ---------------------------------------------------------------------------


def build_cmd(
    model: str,
    allowed_tools: str,
    *,
    mcp_config: str = "",
    add_dir: str = "",
    stream_json: bool = True,
) -> list[str]:
    """Build the review CLI command.

    With stream_json=True, appends ``-- --verbose --output-format stream-json``
    so Claude Code emits one JSON message per turn. The ``--`` separator
    prevents the CLI from consuming ``--verbose`` as its own flag.
    """
    cmd = [
        "claudish",
        "--stdin",
        "--model", model,
        "-y",
        "--allowed-tools", allowed_tools,
    ]
    if mcp_config:
        cmd.extend(["--mcp-config", mcp_config])
    if add_dir:
        cmd.extend(["--add-dir", add_dir])
    if stream_json:
        cmd.extend(["--", "--verbose", "--output-format", "stream-json"])
    return cmd


# ---------------------------------------------------------------------------
# Run the review
# ---------------------------------------------------------------------------


def _print_progress(msg: dict) -> None:
    msg_type = msg.get("type", "")
    if msg_type == "assistant":
        blocks = _unwrap_content_blocks(msg.get("message", {}))
        for block in blocks:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "tool_use":
                print(f"  -> Tool: {block.get('name', 'unknown')}", file=sys.stderr)
            elif block.get("type") == "text":
                text = block.get("text", "")
                if text:
                    preview = text[:120].replace("\n", " ")
                    print(f"  Assistant: {preview}...", file=sys.stderr)
    elif msg_type == "result":
        cost = msg.get("cost_usd", 0)
        turns = msg.get("num_turns", 0)
        print(f"  Result: {turns} turns, ${cost:.4f}", file=sys.stderr)


def save_cli_tokens(started_at: float, tokens_path: str) -> None:
    """Aggregate the CLI's per-process token files into ``tokens_path``.

    The CLI writes real provider token counts to ``~/.claudish/tokens-{pid}.json``
    for each process it spawns (main + subagents). Sum across every file
    written during this run so the summary reflects total usage.
    """
    cli_state_dir = pathlib.Path.home() / ".claudish"
    if not cli_state_dir.is_dir():
        print(f"  No CLI state directory at {cli_state_dir}", file=sys.stderr)
        return

    candidates = [
        p for p in cli_state_dir.glob("tokens-*.json")
        if p.stat().st_mtime >= started_at - 1
    ]
    if not candidates:
        print(f"  No CLI token file found in {cli_state_dir}", file=sys.stderr)
        return

    total = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0, "total_cost": 0.0}
    for src in candidates:
        try:
            data = json.loads(src.read_text())
        except (OSError, json.JSONDecodeError) as e:
            print(f"  Skipping {src}: {e}", file=sys.stderr)
            continue
        total["input_tokens"] += data.get("input_tokens", 0)
        total["output_tokens"] += data.get("output_tokens", 0)
        total["total_tokens"] += data.get("total_tokens", 0)
        total["total_cost"] += data.get("total_cost", 0.0)

    with open(tokens_path, "w") as f:
        json.dump(total, f)
    print(
        f"  Aggregated CLI tokens from {len(candidates)} file(s): "
        f"{total['input_tokens']} in + {total['output_tokens']} out, "
        f"${total['total_cost']:.4f}",
        file=sys.stderr,
    )


def run_review_cli(
    prompt: str,
    *,
    model: str,
    allowed_tools: str,
    messages_path: str,
    tokens_path: str,
    output_path: str,
    mcp_config: str = "",
    add_dir: str = "",
) -> tuple[int, int, list]:
    """Run the review CLI with stream-json capture.

    Streams stdout, parses each line as a JSON message, prints progress to
    stderr, and writes three files on completion:
      * messages_path  — JSON list of parsed stream-json messages
      * tokens_path    — aggregated per-process token totals
      * output_path    — raw stdout (one line per turn)

    Returns ``(returncode, duration_seconds, messages)``.
    """
    cmd = build_cmd(model, allowed_tools, mcp_config=mcp_config, add_dir=add_dir)
    print(f"Starting review CLI with model {model}", file=sys.stderr)

    start_time = time.time()
    messages: list = []
    raw_lines: list[str] = []

    proc = subprocess.Popen(
        cmd,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=sys.stderr,
        text=True,
    )
    assert proc.stdin is not None and proc.stdout is not None
    proc.stdin.write(prompt)
    proc.stdin.close()

    for line in proc.stdout:
        line = line.rstrip("\n")
        if not line:
            continue
        raw_lines.append(line)
        try:
            msg = json.loads(line)
            messages.append(msg)
            _print_progress(msg)
        except json.JSONDecodeError:
            print(line, file=sys.stderr)

    proc.wait()
    save_cli_tokens(start_time, tokens_path)
    duration = int(time.time() - start_time)

    with open(messages_path, "w") as f:
        json.dump(messages, f)
    with open(output_path, "w") as f:
        f.write("\n".join(raw_lines) + "\n")

    print(
        f"Review CLI run completed in {duration}s, {len(messages)} messages",
        file=sys.stderr,
    )
    return proc.returncode, duration, messages


# ---------------------------------------------------------------------------
# Summary helpers
# ---------------------------------------------------------------------------


def format_duration(seconds: int) -> str:
    if seconds <= 0:
        return ""
    mins, secs = divmod(seconds, 60)
    if mins > 0:
        return f"{mins}m {secs}s"
    return f"{secs}s"


def truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n... (truncated, {len(text)} chars total)"


def detect_language(text: str) -> str:
    stripped = text.strip()
    if stripped.startswith("{") or stripped.startswith("["):
        return "json"
    if stripped.startswith("<"):
        return "xml"
    if "def " in stripped[:200] or "import " in stripped[:200]:
        return "python"
    if "function " in stripped[:200] or "const " in stripped[:200]:
        return "javascript"
    return ""


def _unwrap_content_blocks(message) -> list:
    """Unwrap an assistant/user message to a flat list of content blocks.

    Stream-json wraps content in ``{"type": "message", "content": [...]}``.
    Also handles bare dicts and plain lists.
    """
    if isinstance(message, dict):
        if message.get("type") == "message":
            return message.get("content", [])
        return [message]
    if isinstance(message, list):
        return message
    return []


def _is_remote_provider_model(model: str) -> bool:
    """True when the model uses a remote-provider prefix (e.g. ``oai@``).

    The CLI's SSE adapter emits hardcoded placeholder input tokens in
    stream-json for these models, so prefer the per-process tokens file.
    """
    return "@" in model


def load_cli_tokens(tokens_path: str) -> dict:
    try:
        with open(tokens_path) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _extract_model_usage(model_usage: dict) -> tuple[int, int, float]:
    total_in = 0
    total_out = 0
    total_cost = 0.0
    for entry in model_usage.values():
        if not isinstance(entry, dict):
            continue
        total_in += (
            entry.get("inputTokens", 0)
            + entry.get("cacheReadInputTokens", 0)
            + entry.get("cacheCreationInputTokens", 0)
        )
        total_out += entry.get("outputTokens", 0)
        total_cost += entry.get("costUSD", 0.0)
    return total_in, total_out, total_cost


def extract_stats(messages: list, *, model: str, tokens_path: str) -> dict:
    """Extract turns / tool_calls / tokens / cost from a message stream.

    For remote-provider models, tokens and cost come from the CLI's
    per-process tokens file (real provider data). For native Claude Code
    runs, ``modelUsage`` on the result message is authoritative.
    """
    tool_calls = 0
    turns = 0
    cost = 0.0
    input_tokens = 0
    output_tokens = 0

    for msg in messages:
        msg_type = msg.get("type", "")
        if msg_type == "assistant":
            blocks = _unwrap_content_blocks(msg.get("message", {}))
            for block in blocks:
                if isinstance(block, dict) and block.get("type") == "tool_use":
                    tool_calls += 1
        elif msg_type == "result":
            turns = msg.get("num_turns", 0)

    if _is_remote_provider_model(model):
        tokens = load_cli_tokens(tokens_path)
        input_tokens = tokens.get("input_tokens", 0)
        output_tokens = tokens.get("output_tokens", 0)
        cost = tokens.get("total_cost", 0.0)
    else:
        for msg in messages:
            if msg.get("type") == "result":
                model_usage = msg.get("modelUsage", {})
                if model_usage:
                    input_tokens, output_tokens, cost = _extract_model_usage(model_usage)
                break

    return {
        "tool_calls": tool_calls,
        "turns": turns,
        "cost": cost,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
    }


def build_execution_trace(
    messages: list,
    *,
    max_result_chars: int = DEFAULT_MAX_RESULT_CHARS,
    max_param_chars: int = DEFAULT_MAX_PARAM_CHARS,
) -> str:
    """Build a collapsible markdown execution trace from structured messages."""
    parts: list[str] = []

    for msg in messages:
        msg_type = msg.get("type", "")

        if msg_type == "assistant":
            blocks = _unwrap_content_blocks(msg.get("message", {}))
            for block in blocks:
                if not isinstance(block, dict):
                    continue
                if block.get("type") == "text":
                    text = (block.get("text") or "").strip()
                    if text:
                        parts.append(f"\n{text}\n")
                elif block.get("type") == "tool_use":
                    name = block.get("name", "unknown")
                    input_data = block.get("input", {})
                    params_str = ""
                    if input_data:
                        params_str = truncate(
                            json.dumps(input_data, indent=2), max_param_chars
                        )
                    parts.append(f"\n<details>\n<summary>\U0001f527 {name}</summary>\n")
                    if params_str:
                        parts.append(f"\n```json\n{params_str}\n```\n")
                    parts.append("\n</details>\n")

        elif msg_type == "user":
            content_list = _unwrap_content_blocks(msg.get("message", {}))
            for item in content_list:
                if not isinstance(item, dict) or item.get("type") != "tool_result":
                    continue
                content = item.get("content", "")
                if isinstance(content, list):
                    text_parts = [
                        block.get("text", "")
                        for block in content
                        if isinstance(block, dict) and block.get("type") == "text"
                    ]
                    content = "\n".join(text_parts)
                elif not isinstance(content, str):
                    content = str(content)

                is_error = item.get("is_error", False)
                icon = "❌" if is_error else "✅"
                content = truncate(content, max_result_chars)
                lang = detect_language(content)
                parts.append(f"\n<details>\n<summary>{icon} Result</summary>\n")
                parts.append(f"\n```{lang}\n{content}\n```\n")
                parts.append("\n</details>\n")

        elif msg_type == "result":
            result_text = msg.get("result", "")
            if result_text:
                parts.append(f"\n---\n\n**Final Result:**\n\n{result_text}\n")

    return "".join(parts)


def extract_final_result(messages: list) -> str:
    """Return the agent's final human-readable answer from a message stream.

    Prefers the ``result`` field of the terminal ``result`` message (what the
    model returned as its final response). Falls back to the concatenated text
    blocks of the last ``assistant`` message. Returns "" when neither exists.

    This is the text intended for humans (e.g. a Slack summary). Callers must
    never substitute the raw stream-json dump, which interleaves system/tool
    envelopes with the answer and renders as gibberish when posted to Slack.
    """
    for msg in reversed(messages):
        if msg.get("type") == "result":
            result_text = msg.get("result", "")
            if isinstance(result_text, str) and result_text.strip():
                return result_text.strip()
            break

    for msg in reversed(messages):
        if msg.get("type") != "assistant":
            continue
        blocks = _unwrap_content_blocks(msg.get("message", {}))
        texts = [
            block.get("text", "")
            for block in blocks
            if isinstance(block, dict) and block.get("type") == "text"
        ]
        joined = "\n".join(t for t in texts if t).strip()
        if joined:
            return joined
    return ""


def load_raw_output(output_path: str) -> str:
    try:
        with open(output_path) as f:
            return f.read()
    except FileNotFoundError:
        return ""


def build_job_summary(
    *,
    title: str,
    header: str,
    messages: list,
    model: str,
    duration: int,
    tokens_path: str,
    output_path: str,
    extra_metadata_rows: list[tuple[str, str]] | tuple = (),
) -> str:
    """Render a GitHub Actions Job Summary markdown block.

    title:                top heading ("## {title}")
    header:               status header line ("### {header}")
    extra_metadata_rows:  list of (label, value) appended after Model/Duration
                          and before computed stats. Empty values are skipped.
    """
    duration_str = format_duration(duration)
    stats = extract_stats(messages, model=model, tokens_path=tokens_path)

    lines = [
        f"## {title}",
        "",
        f"### {header}",
        "",
        "| | |",
        "|---|---|",
        f"| **Model** | `{model}` |",
    ]
    if duration_str:
        lines.append(f"| **Duration** | {duration_str} |")
    for label, value in extra_metadata_rows:
        if value:
            lines.append(f"| **{label}** | {value} |")
    if stats["turns"]:
        lines.append(f"| **Turns** | {stats['turns']} |")
    if stats["tool_calls"]:
        lines.append(f"| **Tool calls** | {stats['tool_calls']} |")
    if stats["input_tokens"] or stats["output_tokens"]:
        total = stats["input_tokens"] + stats["output_tokens"]
        lines.append(
            f"| **Tokens** | {stats['input_tokens']:,} in + "
            f"{stats['output_tokens']:,} out = {total:,} total |"
        )
    if stats["cost"]:
        lines.append(f"| **Cost** | ${stats['cost']:.4f} |")
    lines.extend(["", "---", ""])

    if messages:
        trace = build_execution_trace(messages)
        if trace.strip():
            lines.append("<details>")
            lines.append(
                f"<summary>Execution trace ({stats['turns']} turns, "
                f"{stats['tool_calls']} tool calls)</summary>"
            )
            lines.extend(["", trace, "", "</details>"])
        else:
            lines.append("*No execution trace available.*")
    else:
        lines.append("*No structured messages captured.*")

    raw = load_raw_output(output_path)
    if raw.strip():
        line_count = len(raw.strip().splitlines())
        clean = re.sub(r"\x1b\[[0-9;]*m", "", raw)
        tail = "\n".join(clean.strip().splitlines()[-500:])
        lines.append("<details>")
        lines.append(f"<summary>Raw output log ({line_count} lines)</summary>")
        lines.extend(["", "```", tail, "```", "", "</details>"])

    return "\n".join(lines) + "\n"


def append_job_summary(summary: str) -> None:
    """Append summary to GITHUB_STEP_SUMMARY if the env var is set."""
    path = os.environ.get("GITHUB_STEP_SUMMARY", "")
    if path:
        with open(path, "a") as f:
            f.write(summary)
