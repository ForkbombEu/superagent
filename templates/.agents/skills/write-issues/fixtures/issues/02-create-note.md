@forkboteu Add `POST /notes` so a client can create a note with a non-empty
title and optional body, persist it, and receive the stored record with an id
(sample-spec: Goals — create). Reject a missing title with HTTP 400 and do
not persist.

- Effort :: ~100k
- Model :: mid
- Context :: src/notes/, tests/test_create_note.py, .superagent/uat.yaml
- Branch after :: inconsistent until list endpoint lands

## Acceptance criteria
- [ ] `POST /notes` with title (and optional body) returns 201 and a record with id
- [ ] Omitting title returns 400 and creates no record
- [ ] Unit tests cover both paths

## User Acceptance Tests
Environment: local

### UAT-1: Create a note
Given the API is running locally
When a client POSTs `/notes` with title "Ship it" and body "first"
Then the response is 201 and the JSON includes a non-empty id and the same title

### UAT-2: Reject missing title
Given the API is running locally
When a client POSTs `/notes` with an empty JSON object
Then the response is 400 and GET `/notes` still returns an empty list

Depends on #1
