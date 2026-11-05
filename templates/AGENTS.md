<!--
Base AGENTS.md for a repository worked on by a dsh-superagent deployment
(https://github.com/ForkbombEu/superagent, templates/AGENTS.md).
Copy it to the repository root, fill in "Project", and keep the rest unless
the deployment changes. The agent account is @forkboteu; replace it everywhere
if the deployment uses another one.
-->
# Agent instructions

## Project

<!-- What the product is, in two or three sentences. -->

Read [docs/specification.md](docs/specification.md) before any task: it is the
authoritative specification, and these instructions only add how work is done.

Project constraints:
<!-- Non-negotiable technical rules: languages, frameworks, mandatory
dependencies, standards to follow, what must never be done. One bullet each. -->

## How work happens

- Work starts from GitHub issues. An issue that mentions `@forkboteu` and is
  opened by an allowed person becomes a task for the dsh-superagent agent, which
  implements it on a branch and opens one pull request for it.
- Claude Code reviews every pull request the agent opens. The agent fixes the
  blocking findings until the review passes, then squash-merges the pull
  request itself once the required CI checks pass.
- No human approval is needed for any step. Issues run in the order their
  `Depends on` lines give, and independent issues run in parallel.
- A person stops or redirects work with a comment on the issue or pull request.

## Code and tests

- Every change comes with tests that exercise it, and the existing tests pass.
- The toolchain (runtimes, package managers, CLIs) is pinned in `mise.toml` at
  the repository root, and CI uses the same versions. Change a version there,
  in the same pull request as anything that needs it.
- Keep changes within the scope of their issue; record anything else found on
  the way as a new issue instead of fixing it in the same pull request.
- Never commit secrets, credentials or personal data, and never deploy to
  production.

## Commits and pull requests

- Commit messages and pull request titles follow
  [Conventional Commits](https://www.conventionalcommits.org/en/v1.0.0/):
  `<type>(<optional scope>): <description>`, with type one of `feat`, `fix`,
  `docs`, `style`, `refactor`, `perf`, `test`, `build`, `ci`, `chore` or
  `revert`; `!` and a `BREAKING CHANGE:` footer mark a breaking change. The
  squash merge uses the pull request title as the commit subject.
- One pull request per issue. Its description says `Closes #<n>` for the issue
  it completes, and `Refs #<n>` for an issue it only advances.

## Merging

The agent account `@forkboteu` squash-merges a pull request it opened, without
further human approval, once an independent Claude Code review of the pull
request's exact head commit passes and all required CI checks pass
(`gh pr merge --squash --match-head-commit <reviewed sha>`). It never merges
any other pull request, never uses `--admin` or bypasses branch protection, and
does not merge work whose issue or comment explicitly forbids merging. Every
other merge needs human approval.

## Writing issues

These rules are for anyone, person or agent, who opens issues for this
repository; an agent asked to plan a feature or a backlog opens the issues
following them.

Every issue:

- Is opened by a person's account that the deployment allows to request work,
  never by `@forkboteu`: the agent does not take requests from itself. An agent
  opening issues does so with that person's `gh`.
- Mentions `@forkboteu` in the description it is created with. A mention added
  later by editing the description is not seen; post a comment instead.
- Asks for exactly one pull request's worth of work: one module or feature
  slice, reviewable on its own, typically up to a few hundred lines. Split
  anything larger, such as a redesign across several modules.
- States the acceptance criteria as checks a reviewer can verify, including the
  tests to add.
- Lists its prerequisites on one line, `Depends on #<a>, #<b>`, naming only the
  issues whose merged code it needs. Omit the line when there are none. An
  issue starts once every listed issue is closed as completed; one closed as
  not planned blocks it.
- Never asks the agent to wait, not to begin, not to merge or to get approval:
  the agent obeys such a request and does not lift it by itself.

Opening a backlog:

1. Break the work into issues as above and order them so each one's
   prerequisites come before it; there must be no cycles.
2. Make the first issue set up the build, the test suite and a CI workflow,
   and make that workflow's checks required on the default branch, so every
   later merge has to pass them.
3. Create the issues in that order with
   `gh issue create --repo <owner>/<repo> --title "<title>" --body-file <file>`,
   so each `Depends on` line names issues that already exist.
4. Open them all at once: the agent starts every issue whose prerequisites are
   done, up to its concurrency limit, and the rest as their prerequisites merge.

Issue template:

```markdown
@forkboteu <one paragraph: what to build and why, referring to the relevant
sections of docs/specification.md>

## Acceptance criteria
- [ ] <verifiable behavior>
- [ ] <tests that cover it>

Depends on #<a>, #<b>
```

The title is a short imperative summary, such as
`Add the rulebook import endpoint`; the agent writes the conventional pull
request title from it.
