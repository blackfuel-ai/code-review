import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))

from fetch_reply_comments import build_mention_pattern, filter_mentions


CODE_PATTERN = build_mention_pattern("code-reviewer")
OTHER_PATTERN = build_mention_pattern("other-reviewer")


class TestFilterMentions:
    def test_keeps_comment_with_handle_mention(self):
        comments = [{
            "body": "Hey @code-reviewer I disagree with this finding.",
            "user": {"login": "alice"},
            "created_at": "2026-04-17T10:00:00Z",
            "html_url": "https://example.com/1",
        }]
        out = filter_mentions(comments, CODE_PATTERN)
        assert len(out) == 1
        assert out[0]["author"] == "alice"
        assert "disagree" in out[0]["body"]

    def test_different_handles_do_not_cross_match(self):
        comments = [{
            "body": "@other-reviewer please double-check this bit.",
            "user": {"login": "alice"},
        }]
        assert filter_mentions(comments, CODE_PATTERN) == []
        assert len(filter_mentions(comments, OTHER_PATTERN)) == 1

    def test_drops_comment_without_mention(self):
        comments = [{
            "body": "Just a regular comment with no mention.",
            "user": {"login": "alice"},
        }]
        assert filter_mentions(comments, CODE_PATTERN) == []

    def test_case_insensitive_mention(self):
        comments = [{
            "body": "@Code-Reviewer please re-check",
            "user": {"login": "bob"},
        }]
        assert len(filter_mentions(comments, CODE_PATTERN)) == 1

    def test_does_not_match_handle_extension(self):
        # ``@code-reviewer-other`` must NOT match because of the negative
        # lookahead after the handle in the pattern built by build_mention_pattern.
        comments = [{
            "body": "@code-reviewer-other is a different bot",
            "user": {"login": "eve"},
        }]
        assert filter_mentions(comments, CODE_PATTERN) == []

    def test_matches_when_prefixed_with_non_handle_char(self):
        # The pattern only uses a negative lookahead AFTER the handle, not
        # before, so leading characters still allow a match. Intentional —
        # we only care that the mention appears somewhere in the body.
        comments = [{
            "body": "x@code-reviewer in the middle still matches",
            "user": {"login": "eve"},
        }]
        assert len(filter_mentions(comments, CODE_PATTERN)) == 1

    def test_ignores_non_string_body(self):
        comments = [{"body": None, "user": {"login": "x"}}]
        assert filter_mentions(comments, CODE_PATTERN) == []

    def test_missing_user_defaults_to_unknown(self):
        comments = [{"body": "@code-reviewer hi", "user": None}]
        out = filter_mentions(comments, CODE_PATTERN)
        assert out[0]["author"] == "unknown"

    def test_preserves_multiple_matches_in_order(self):
        comments = [
            {"body": "@code-reviewer first", "user": {"login": "a"}},
            {"body": "no mention here", "user": {"login": "b"}},
            {"body": "@code-reviewer third", "user": {"login": "c"}},
        ]
        out = filter_mentions(comments, CODE_PATTERN)
        assert [r["author"] for r in out] == ["a", "c"]


class TestBuildMentionPattern:
    def test_escapes_regex_metacharacters(self):
        pattern = build_mention_pattern("code.reviewer")
        assert pattern.search("@code.reviewer done") is not None
        assert pattern.search("@codeXreviewer done") is None

    def test_negative_lookahead_blocks_handle_suffix(self):
        pattern = build_mention_pattern("code-reviewer")
        assert pattern.search("@code-reviewer x") is not None
        assert pattern.search("@code-reviewer-other x") is None
        assert pattern.search("@code-reviewer0 x") is None
