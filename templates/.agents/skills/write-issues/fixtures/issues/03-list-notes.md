@forkboteu Add `GET /notes` that returns the current test user's notes in
creation order, and restore feature consistency so README, `.env.example` and
UAT setup agree with the running API (sample-spec: Goals — list).

- Effort :: ~90k
- Model :: mid
- Context :: src/notes/, tests/test_list_notes.py, README.md, .env.example
- Branch after :: consistent

## Acceptance criteria
- [ ] `GET /notes` returns created notes in creation order
- [ ] Unit tests cover empty and non-empty lists
- [ ] README and `.env.example` describe how to run the API locally for UAT

## User Acceptance Tests
Environment: local

### UAT-1: List in creation order
Given two notes were created via `POST /notes`
When a client GETs `/notes`
Then the response is 200 and the titles appear in creation order

Depends on #2
