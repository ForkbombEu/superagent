@forkboteu Set up the Notes API repository toolchain so every later merge
runs the same tools. Add `mise.toml` with Python 3.12, a minimal package
layout, pytest, and a CI workflow that installs via mise and runs the tests;
make that workflow required on the default branch (sample-spec: Constraints).

- Effort :: ~80k
- Model :: low
- Context :: mise.toml, .github/workflows/test.yml, pyproject.toml
- Branch after :: consistent

## Acceptance criteria
- [ ] `mise.toml` pins Python 3.12 and exposes a test task
- [ ] CI installs with mise and runs pytest on every pull request
- [ ] The test workflow is required on the default branch
- [ ] Tests cover that `mise run test` exits 0 on an empty suite
