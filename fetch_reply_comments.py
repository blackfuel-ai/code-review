#!/usr/bin/env python3
"""Fetch PR comments that @-mention a given reviewer handle.

Runs before run_review.py so the review LLM can see notes from human
reviewers targeted at the configured reviewer handle.

The notes are fed into the review prompt as ADVISORY context, not as
authoritative directives — humans cannot override findings by posting
a comment, they can only provide hints the reviewer weighs against the
code.

Filter rule: keep any PR issue_comment whose body contains
``@<handle>`` (case-insensitive, word-boundary guarded so that
``@<handle>-other`` does not match).

Required env:
    GITHUB_REPOSITORY   owner/repo
    PR_NUMBER           Pull request number
    REPLY_HANDLE        Reviewer handle to match (without the @),
                        e.g. ``code-reviewer``
    REPLY_OUTPUT_FILE   Where to write the filtered replies JSON

Optional env:
    GITHUB_OUTPUT       GitHub Actions output file for reply_count/char_count
"""

import json
import os
import re
import subprocess
import sys


def build_mention_pattern(handle: str) -> re.Pattern:
    """Build a case-insensitive regex that matches ``@<handle>`` as a whole word.

    The negative lookahead prevents ``@code-reviewer`` from matching inside
    ``@code-reviewer-other`` (GitHub usernames are ``[A-Za-z0-9-]+``).
    """
    return re.compile(rf"@{re.escape(handle)}(?![A-Za-z0-9-])", re.IGNORECASE)


def fetch_issue_comments(repo: str, pr_number: str) -> list[dict]:
    """Fetch all PR conversation (issue) comments via ``gh api --paginate``."""
    result = subprocess.run(
        ["gh", "api", f"repos/{repo}/issues/{pr_number}/comments", "--paginate"],
        capture_output=True, text=True, check=True, timeout=120,
    )
    return json.loads(result.stdout)


def filter_mentions(comments: list[dict], pattern: re.Pattern) -> list[dict]:
    """Keep only comments whose body matches the given mention pattern."""
    replies = []
    for c in comments:
        body = c.get("body")
        if not isinstance(body, str):
            continue
        if not pattern.search(body):
            continue
        user = c.get("user") or {}
        replies.append({
            "author": user.get("login", "unknown"),
            "created_at": c.get("created_at", ""),
            "url": c.get("html_url", ""),
            "body": body,
        })
    return replies


def write_github_output(reply_count: int, char_count: int) -> None:
    """Write summary counts to ``$GITHUB_OUTPUT`` for downstream steps."""
    github_output = os.environ.get("GITHUB_OUTPUT", "")
    if not github_output:
        return
    with open(github_output, "a") as f:
        f.write(f"reply_count={reply_count}\n")
        f.write(f"char_count={char_count}\n")


def main() -> int:
    repo = os.environ["GITHUB_REPOSITORY"]
    pr_number = os.environ["PR_NUMBER"]
    handle = os.environ["REPLY_HANDLE"]
    output_file = os.environ["REPLY_OUTPUT_FILE"]

    pattern = build_mention_pattern(handle)
    comments = fetch_issue_comments(repo, pr_number)
    replies = filter_mentions(comments, pattern)

    with open(output_file, "w") as f:
        json.dump(replies, f, indent=2)

    char_count = sum(len(r["body"]) for r in replies)
    print(
        f"Fetched {len(replies)} reply comment(s) mentioning "
        f"@{handle} ({char_count} chars total) -> {output_file}",
        file=sys.stderr,
    )
    for r in replies:
        print(
            f"  - @{r['author']} at {r['created_at']} ({len(r['body'])} chars)",
            file=sys.stderr,
        )

    write_github_output(len(replies), char_count)
    return 0


if __name__ == "__main__":
    sys.exit(main())
