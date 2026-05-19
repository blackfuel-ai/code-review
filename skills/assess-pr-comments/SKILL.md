---
name: assess-pr-comments
description: Fetch and assess all PR review comments — automated reviewers (CodeRabbit, Claude Code, bf-review), CI bots, and human reviewers. Use when asked to triage PR feedback, assess findings, or roll resolved items back into the sticky review comment.
allowed-tools: Bash, Read, Glob, Edit
allowed-bash-commands: gh api:*, gh pr view:*, gh run list:*, gh run rerun:*, python3:*
---

# Assess PR Comments

Fetch and assess every review comment on a GitHub pull request, including feedback from automated reviewers and humans, then triage them into a structured assessment table.

## Process

1. **Fetch all PR feedback — every body, not just the inventory.**

   This is the most failure-prone step. The anti-pattern is: list IDs from the three endpoints, fetch the body of the most recent verdict or summary comment, and assess from that. **Do not do this.** Verdict reviews can omit findings, downgrade them silently across iterations, or summarize a chain of sticky-comment updates without showing what changed. Trust the source, not the summary.

   **Mandatory procedure — one Python script, three endpoints, every body:**

   ```bash
   python3 <<'PY'
   import json, subprocess
   def fetch(path):
       out = subprocess.run(["gh","api",path,"--paginate"], capture_output=True, text=True)
       return json.loads(out.stdout.encode("utf-8","surrogateescape").decode("utf-8","replace"))
   REPO = "{owner}/{repo}"; PR = {PR_NUMBER}
   inline  = fetch(f"repos/{REPO}/pulls/{PR}/comments")
   reviews = fetch(f"repos/{REPO}/pulls/{PR}/reviews")
   issues  = fetch(f"repos/{REPO}/issues/{PR}/comments")
   print(f"COUNTS  inline={len(inline)}  reviews={len(reviews)}  issues={len(issues)}")
   for kind, items in [("inline", inline), ("review", reviews), ("issue", issues)]:
       for c in items:
           body = c.get("body") or ""
           meta = c.get("path","") + (":" + str(c.get("line","")) if "path" in c else "") + (f"  state={c.get('state')}" if "state" in c else "")
           print(f"\n===== {kind} {c['id']}  {c['user']['login']}  {meta}  body_len={len(body)} =====")
           print(body)
   PY
   ```

   Why a single script: `gh api` returns JSON with occasional invalid surrogates (the `encode/surrogateescape/decode/replace` chain handles them). Doing it inline-per-call multiplies failure surface. Running once produces a complete, indexed dump you can scroll through.

   **Completeness gate — before writing any assessment, prove you read every body:**

   - State the three counts (`inline=N reviews=N issues=N`) and the total.
   - In your assessment, every ID listed in the inventory must appear in the output table (assessed, resolved, or explicitly skipped with a reason like "informational" / "linkback" / "coverage report").
   - If the table has fewer rows than `total - obvious-non-findings`, you shortcutted. Go back and read the missed bodies.

   **Read all verdict chains, not just the latest.** When the same reviewer has posted multiple reviews across iterations, read all of them in order. The earliest one often lists the original finding count; later ones show what was resolved, downgraded, or newly surfaced. Discrepancies between iterations are evidence — for example, "earlier verdict cited *two* HIGHs; latest cites *one*" tells you a finding was downgraded, and you should locate where and why.

2. **Identify reviewer type** for each comment (check `user.login` in the API response):

   - **coderabbitai[bot]**: CodeRabbit AI reviewer
   - **claude[bot]**: Claude Code AI reviewer
   - **github-actions[bot]**: GitHub Actions bot (CI checks, automated comments)
   - **Other**: human reviewers

3. **Handle multi-part AI reviews**:

   - AI reviewers often post multiple sequential issue_comments (5–15 per review iteration).
   - Each comment may contain multiple findings; parse the FULL body of each comment to extract all specific issues.
   - Findings are usually under markdown sections like "## Issues & Recommendations" with numbered items — each numbered item is a separate finding.
   - Always fetch the full `body` field. Do not truncate or summarize.

4. **For each finding extracted**, assess:

   - **Validity**: is the finding correct?
   - **Severity**: Critical / Major / Minor / Nitpick
   - **Action required**: fix needed / already addressed / won't fix (with reason)
   - **Implementation**: if a fix is needed, provide the specific code change

5. **Generate a summary table** with every finding and its status.

   - Multiple findings from the same comment list as separate rows.
   - Use the same comment ID for all findings from that comment.
   - Add a short finding description per row.

6. **Patch the sticky review comment** to mark resolved findings as `[x]`.

   The bf-review action posts a sticky comment with an HTML marker. Before applying fixes, update that comment so the next review cycle carries forward what is already resolved.

   **Sticky marker:** `<!-- bf-review-code-report -->`

   ```bash
   # 1. Fetch all issue comments and find the sticky one
   COMMENTS=$(gh api repos/{owner}/{repo}/issues/{PR_NUMBER}/comments --paginate)

   # 2. Find the oldest matching comment (same logic as the action's run_review.py)
   COMMENT_ID=$(echo "$COMMENTS" | python3 -c "
   import sys, json
   marker = '<!-- bf-review-code-report -->'
   comments = json.load(sys.stdin)
   matching = sorted(
       (c for c in comments if isinstance(c.get('body'), str) and c['body'].startswith(marker)),
       key=lambda c: c.get('created_at', ''),
   )
   if matching:
       print(matching[0]['id'])
   ")

   # 3. Fetch the comment body, mark resolved findings, and PATCH
   # For each resolved finding:
   #   a. Replace '- [ ]' with '- [x]' for the specific line
   #   b. Append a resolution note on the next line explaining WHY it was resolved
   BODY=$(gh api repos/{owner}/{repo}/issues/comments/{COMMENT_ID} --jq '.body')
   PATCHED_BODY=$(echo "$BODY" | python3 -c "
   import sys
   body = sys.stdin.read()
   # Each entry is (finding_text, resolution_note)
   # resolution_note explains why: false positive, pre-existing, intentional, won't fix, or fixed
   resolved_findings = [
       ('finding text from assessment table', 'false positive: Django auto-escapes all template variables'),
       ('another finding text', 'pre-existing: this field was already exposed before this PR'),
   ]
   for finding, note in resolved_findings:
       old = f'- [ ] {finding}'
       new = f'- [x] {finding}\n  > **Resolved — {note}**'
       body = body.replace(old, new, 1)
   print(body)
   ")

   # 4. PATCH the comment
   gh api repos/{owner}/{repo}/issues/comments/{COMMENT_ID} \
     -X PATCH \
     -f body="$PATCHED_BODY"
   ```

   **Match findings precisely** — use the exact checkbox text from the sticky comment. Do not blindly replace all `[ ]` with `[x]`.

   **Resolution notes are mandatory** — every checked finding must have a `> **Resolved — {reason}**` line below it. Common reasons:

   - `false positive: {technical explanation}` — the finding is incorrect
   - `pre-existing: {explanation}` — not introduced by this PR
   - `intentional: {explanation}` — deliberate design choice
   - `won't fix: {explanation}` — valid but out of scope
   - `fixed in {commit}` — code was changed to address the finding

7. **Respond to false positives**: when a finding from `@coderabbitai` is a false positive, reply to the specific comment explaining why:

   ```bash
   gh api repos/{owner}/{repo}/pulls/{PR_NUMBER}/comments/{COMMENT_ID}/replies \
     -X POST \
     -f body="$(cat <<'EOF'
   @coderabbitai This is a false positive.

   **Reason**: {explanation of why the finding is incorrect}

   {technical details if applicable}
   EOF
   )"
   ```

   **Feeding advisory context back into the next bf-review run:** when the false positive (or any guidance the reviewer should see next time) comes from the bf-review sticky comment, also @-mention the configured `reviewer-handle` (default `@code-reviewer`; override via the action's `reviewer-handle` input) somewhere in your reply. The action's `fetch_reply_comments.py` step picks those mentions up and feeds them into the next review prompt as ADVISORY context. The reviewer LLM weighs the note against the code and may disagree — notes do not override findings.

8. **Apply fixes** if requested.

9. **Re-trigger the review workflow** so it re-reviews with the fixes applied:

   ```bash
   # Find the latest workflow run for this PR's branch and re-run it.
   BRANCH=$(gh pr view {PR_NUMBER} --json headRefName -q '.headRefName')
   RUN_ID=$(gh run list --branch="$BRANCH" --limit=1 --json databaseId -q '.[0].databaseId')
   gh run rerun "$RUN_ID"
   ```

   After re-triggering, the workflow re-runs the review against the fixed code, PATCHes the sticky comment with fresh findings, and re-runs the verdict step.

## Output Format

```markdown
## PR #{number} - Review Comments Assessment

| #   | ID         | Source        | Reviewer          | Type   | Finding     | Validity | Severity | Action   |
| --- | ---------- | ------------- | ----------------- | ------ | ----------- | -------- | -------- | -------- |
| 1   | 2564535928 | comment       | @coderabbitai[bot]| AI     | README missing port in URLs | OK | Minor    | Fix |
| 2   | 1234567890 | review        | @claude[bot]      | AI     | Test coverage gap | OK | Major    | Fix      |
| 3   | 3693276867 | issue_comment | @claude[bot]      | AI     | Hardcoded admin credentials need warning | OK | Minor    | Fix |
| 4   | 9876543210 | comment       | @human-user       | Human  | Security concern | OK | Critical | Fix      |

### Finding 1: {Title}

**ID**: {id}
**Source**: comment / review / issue_comment
**URL**: {html_url}
**Reviewer**: @user
**File**: path/to/file.py:L{line}
**Finding**: {description}
**Assessment**: {your analysis}
**Action**: {Fix needed / Already addressed / Won't fix}
{code fix if applicable}
```

## Notes

- **Source types** (matching GitHub API endpoints):
  - `comment`: pull request review comments on code lines (`/pulls/{PR}/comments`, URL fragment `#discussion_r{ID}`)
  - `review`: full review submissions (`/pulls/{PR}/reviews`, URL fragment `#pullrequestreview-{ID}`)
  - `issue_comment`: PR conversation comments (`/issues/{PR}/comments`, URL fragment `#issuecomment-{ID}`)
- **ID**: numeric ID from the API response (`id` field).
- **Multiple findings per comment**: a single `claude[bot]` issue_comment may contain 3–10+ individual findings. Parse the full body and create one table row per finding, all sharing the same comment ID.
- **Replying**: only `comment` IDs support replies via `/pulls/{PR}/comments/{ID}/replies`. Reviews and issue comments cannot be replied to directly — post a new comment instead.
- **URL**: use the `html_url` field from the API response.
