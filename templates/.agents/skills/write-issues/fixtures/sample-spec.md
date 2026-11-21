# Notes API (sample specification)

A tiny HTTP API for personal notes. Used only as a dry-run input for the
write-issues skill.

## Goals

- Create a note with a non-empty title and optional body; persist it; return
  it with an id.
- Reject create requests that omit the title with HTTP 400 and no persistence.
- List notes for the current test user in creation order.

## Constraints

- Python 3.12, mise-managed toolchain, pytest for unit tests.
- Local UAT against `http://127.0.0.1:3000` with `.superagent/uat.yaml`.
