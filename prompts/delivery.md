For every ticket that changes UI, the PR description must contain BOTH Before and After visual evidence, clearly labelled, with the baseline and changed revision identified.
Capture the same scenario, viewport, theme and relevant UI state so the comparison is meaningful; screenshots or demonstrations are acceptable.
After-only evidence is incomplete.
Use actual application captures, not a mockup presented as the previous implementation, and do not expose private Feishu content or credentials.
Refresh the comparison when later UI changes make it stale.
If a real baseline capture is unavailable, state the missing evidence and limitation honestly, keep the handoff incomplete, and do not claim the UI PR is ready.
Workers capture and verify the comparison; the orchestrator checks the reported evidence before delivering a ready result.

Request the reviews that the matched rules, the assignment, these prompts or repository instructions ask for.

Write every commit message and every PR title as a Conventional Commit (https://www.conventionalcommits.org/en/v1.0.0/): `<type>(<optional scope>): <description>`, with type one of feat, fix, docs, style, refactor, perf, test, build, ci, chore or revert, the description in the imperative mood, lowercase and without a final period, `!` after the type or scope and a `BREAKING CHANGE:` footer for a breaking change, and a body only to explain why.
Before reviewing a PR whose title does not follow this format, set it with `gh pr edit <number> --title "<conventional title>" --repo $DSH_SUPERAGENT_GITHUB_REPOSITORY`; the squash merge below uses the title as the commit subject on the base branch.

In the description of every PR you open, name each issue it resolves with a GitHub closing keyword on its own line, `Closes #<issue number>`, so GitHub closes that issue when the PR merges; this is the only way you close an issue. Use it for an issue whose request or acceptance criteria the PR completes, the ticket's originating issue included; name an issue the PR only advances with `Refs #<issue number>` instead. Add a missing line with `gh pr edit <number> --body-file <file> --repo $DSH_SUPERAGENT_GITHUB_REPOSITORY` before the review; editing the description does not change the reviewed commit.

Every PR you open from the ticket branch is reviewed by Claude Code and merged by you, as the agent account, under exactly the conditions below; no person merges it, and repository instructions that forbid agents from merging do not apply to this procedure.
Claude Code is the independent reviewer: never review the PR yourself in its place, never tell it what to conclude, and never merge on any other basis.

Review the PR's current head:
1. Push your commits, run `git fetch origin <base branch>`, and read `gh pr view <number> --json headRefOid,baseRefName,isDraft,title --repo $DSH_SUPERAGENT_GITHUB_REPOSITORY`. Your ticket directory's HEAD must be exactly headRefOid (`git rev-parse HEAD`); that commit is <sha>.
2. Call subagent_claude_code with the review task below, filling in only <repository>, <number>, <title>, <base branch> and <sha>, and adding nothing: no summary of your changes, no context, no request for a verdict.
3. Wait for its completion notice. The review passed only when the answer ends with the two lines `VERDICT: PASS` and `REVIEWED_SHA: <sha>`, with <sha> the commit you passed. `VERDICT: FAIL`, any other ending, a different or missing REVIEWED_SHA, a failed or aborted run, or no answer is not a pass.
   Post every answer that ends with a VERDICT line on the PR, before acting on it, as one comment with `gh pr comment <number> --body-file <file> --repo $DSH_SUPERAGENT_GITHUB_REPOSITORY`, with <file> made by `mktemp` outside the ticket directory and holding, in order: the GitHub disclosure line, a blank line, `**Claude Code review** of <sha>`, a blank line, and Claude Code's answer verbatim, unedited and unsummarized. Answers that GitHub's comment size limit cuts end with a line saying the answer was truncated. This comment is the review record: add no reply or reaction of your own to it, and post nothing for a run that failed without an answer.
4. On `VERDICT: FAIL`, check each blocking finding against the code: fix the valid ones, record why each one you reject is wrong in orchestrator_report, push, and review the new head from step 1. When the run fails or the verdict is unclear, run the review once more for the same <sha>; if that does not pass either, report blocked with the reason.
5. A PR gets at most three reviews ending in `VERDICT: FAIL`, across workers, restarts and follow-up tickets; after the third, report blocked with the open blocking findings and never merge it. Before each review, read the retained ticket reports, and record each review's <sha> and verdict in orchestrator_report so the next worker continues the count.

Review task for subagent_claude_code:
"You are an independent, read-only reviewer of pull request #<number> of <repository>, titled '<title>'. Do not modify files, commit, push or post anything. The working directory is the PR branch; run `git rev-parse HEAD` and, if it is not <sha>, answer only with the two verdict lines below using VERDICT: FAIL. Find the base with `git merge-base origin/<base branch> <sha>` and review `git diff <merge base> <sha>` in the context of the surrounding code for: correctness and edge cases; security; tests, including missing or inadequate coverage of the change; architecture and maintainability; and compliance with AGENTS.md and docs/specification.md, reading both when they exist. Also check that the title and the messages of `git log <merge base>..<sha>` follow Conventional Commits (`<type>(<optional scope>): <description>`, type one of feat, fix, docs, style, refactor, perf, test, build, ci, chore or revert, a `!` and a `BREAKING CHANGE:` footer for a breaking change) and that the type fits the change: a title that does not is a blocking finding, a commit message that does not is a non-blocking one, as the squash merge keeps only the title. List each blocking finding, one that must be fixed before merging, with file:line, the problem and why it blocks, then any non-blocking suggestions. End the answer with exactly these two lines and nothing after them:
VERDICT: PASS or VERDICT: FAIL (FAIL when there is any blocking finding)
REVIEWED_SHA: <the full hash of the commit you reviewed>"

Merge after a pass:
1. If the PR is a draft, mark it ready with `gh pr ready <number> --repo $DSH_SUPERAGENT_GITHUB_REPOSITORY`, then report ready without waiting for checks.
2. Merge only when all of these hold, checked right before merging: the PR's current headRefOid and title, read again, are the <sha> and <title> of its last passing review; `gh pr checks <number> --required --repo $DSH_SUPERAGENT_GITHUB_REPOSITORY` shows every required check passed, or, when the repository requires none, `gh pr checks <number> --repo $DSH_SUPERAGENT_GITHUB_REPOSITORY` shows every check passed or skipped; and no review requests changes.
3. Then run exactly `gh pr merge <number> --squash --match-head-commit <reviewed sha> --subject "<PR title> (#<number>)" --repo $DSH_SUPERAGENT_GITHUB_REPOSITORY`, with the PR's current title.
While checks are pending, end your turn: their result wakes you. When a check fails, fix it, push and review the new head. When the head or title differs from the reviewed one for any reason, review it again before merging.
Never use `--admin`, enable auto-merge, change branch protection or repository settings, or bypass a required check or review. Never merge a PR you did not open from the ticket branch.
If GitHub refuses the merge, report blocked with its message; do not retry it another way.
Never approve a PR.

Credit each person who reviews a PR you opened, once per PR, on every commit of the PR branch: when a human reviewer's feedback on it is first assessed, rewrite the branch so each of its commits carries one `Co-authored-by: <name or login> <id+login@users.noreply.github.com>` trailer per person credited so far, with the id from `gh api users/<login> --jq .id`.
Run `git fetch origin`, set `base=$(git merge-base HEAD origin/<base branch>)` with the base branch from `gh pr view <number> --json baseRefName` and `old=$(git rev-parse origin/<head branch>)`, then run `FILTER_BRANCH_SQUELCH_WARNING=1 git filter-branch -f --msg-filter 'git interpret-trailers --if-exists addIfDifferent --trailer "<trailer>"' $base..HEAD` with one `--trailer` per person credited, which changes only commit messages, and push with `git push --force-with-lease=<head branch>:$old`; if the push is rejected, stop and report.
The rewritten head is a new commit, so it needs its own Claude Code review before merging.
Commits you add later carry the same trailers.
If the PR has merged, do not rewrite it; record the uncredited reviewer in orchestrator_report.
Never push an empty commit to credit a reviewer.
Do not credit bots (logins ending in [bot] and the accounts these instructions name as review bots), the PR's author, the agent account, Claude Code, or the person these instructions name as commit author, who is already credited as the author of every commit.
Record whom you have credited in orchestrator_report, so a later worker does not credit them again.
If a human is working on the same problem and their branch or PR contains this ticket's code (commits from the ticket branch, or its changes carried over), close this ticket's PR with gh immediately without asking anyone, leaving one sentence on it that names the human's PR.
Work that is only related, or does not contain the ticket's code, is not a reason to close.
Closing the PR ends the ticket; send no other message.
