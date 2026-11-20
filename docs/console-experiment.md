# Console experiment: chat with Opus from the DSH panel

A work order for one agent and one pull request. It ships the whole console
now, as an experiment, ahead of [feature-release-plan.md](feature-release-plan.md)
F7. That plan stays as written; if the experiment holds, F7 is re-planned from
what landed here.

- **Branch:** `feat/console` from `attesta`.
- **PR:** into `attesta`, titled `feat(console): chat with Opus about the deployment's design`.
  A person decides the merge; Coolify redeploys `attesta` on push.
- **Settled, not to reopen:** the chat is a hosted service (a Vite + React page
  with assistant-ui, served by a Hono server that also runs the AI SDK) and DSH
  only gets a doorway plugin. No Next.js and no other full-stack framework.
  D5 (one `ANTHROPIC_API_KEY`) and D6 (gates stay CLI checks) apply.

## Shape

```
DSH web UI ── left-sidebar icon ── main panel: <iframe src="/console/">
                                          │ same origin, DSH session cookie
dashboard (Caddy :8080) ── /console* ── forward_auth superagent:3083 ── console:3000
                                                                         │ streamText
console (Hono: dist/ + /console/api/*) ── Anthropic API (ANTHROPIC_API_KEY)
        └─ tools: GET http://dashboard:8080/orchestrator/api/* with the caller's cookie
```

## Facts this plan rests on

Checked in DSH `0.2.2-rc.5` (`2e39e70`) and the Superagent prototype (`8c32a9d`):

- **Main panel, not a right-sidebar tab.** The right sidebar exists only while a
  Session's conversation fills the main column: no surface on the hero screen,
  and none while a global panel (the Superagent panel included) replaces it
  (`packages/client/ui-sidebar-right/README.md`, "`mounted`" and Known
  Limitations). The Superagent plugin's own `apply` is the model to copy: `main`
  slot + `sidebar.panellist` icon (`src/client/index.tsx`).
- **The built-in Browser tab cannot host it**: it rejects the DSH application
  origin (`ui-sidebar-browser` README, Protocol policy). The doorway draws its
  own iframe.
- **The cookie reaches the iframe.** The DSH session cookie is `Path=/; HttpOnly;
  SameSite=Strict` (`packages/host/authentication-token/src/browser-auth.ts`), so
  a same-origin iframe and its `fetch` carry it and Caddy's `forward_auth` works
  unchanged.
- **Client bundle format.** A client plugin is a package whose `package.json`
  has `dsh.client` (`platform`, `inject`) and `exports["./client"]`, and whose
  client file is CJS wrapped in `window.__ModuleLoader__.load({ id, factory:
  (require) => … })`, with `react`, `react/jsx-runtime` and `react-dom` from the
  host (prototype `package.json` and `tsdown.config.ts`). It exports `inject`
  and `apply(ctx)`.
- **The management API trusts loopback-looking requests** (see
  `dashboard/Caddyfile`). The console reaches it only through the dashboard's
  Caddy route with the caller's cookie, never `superagent:3083` with a forged
  Host, so the cookie gate stays the only way in.

## Files

New: `console/` (service), `dsh/console-plugin/` (doorway),
`uat/tests/test_console_proxy.py`, `uat/tests/test_console_plugin.py`.
Changed: `compose.yaml`, `dashboard/Caddyfile`, `Dockerfile` (plugin add block
only), `.github/workflows/test.yml`, `README.md` (new section), `.env.example`.

## Steps

Each step ends green on `unit`, `docker`, `build` and the new `console` job.
Commit per step (Conventional Commits); push and open the PR as a draft after
step 1 so CI runs on every push.

### 1. Service behind the dashboard

- `console/web/`: a Vite + React page (`base: '/console/'`) rendering
  assistant-ui's `<Thread />` with its AI SDK runtime; the chat transport's API
  URL is `/console/api/chat`. Follow assistant-ui's own setup docs for a plain
  React app rather than its Next.js template.
- `console/server.ts`: a Hono app on `@hono/node-server`, port 3000: serves
  `web/dist` under `/console/` (SPA fallback to `index.html`) and mounts
  `/console/api/*`. Export the app so tests call `app.request()` without a
  socket. `console/Dockerfile`: multi-stage on `node:24-alpine` (build the page,
  then run the server with production dependencies only), non-root user.
- `compose.yaml`: service `console`, `build: ./console`, `expose: ["3000"]`,
  a `healthcheck` on a `/console/api/health` route, no Coolify domain variable.
  `dashboard` does not `depends_on` it: a broken console must not block the
  deploy of the rest.
- `dashboard/Caddyfile`: `@console path /console /console/*`, placed before the
  catch-all, with the same `forward_auth superagent:3083 { uri /? }` and the
  same `Sec-Fetch-Site` 403 as `@management`; `reverse_proxy console:3000` with
  Host and Origin kept.
- CI: a `console` job (`npm ci`, `npm test`, `npm run build` in `console/`);
  the `build` job builds `superagent console dashboard`.
- **Test** `uat/tests/test_console_proxy.py` (Docker, skipped like the others
  without it): run the real Caddyfile on a test network with a fake `superagent`
  (200 on `/` with a given cookie, 401 otherwise) and a fake `console`.
- **Done when:** `/console/` → 401 without the cookie, 200 with it; a
  `Sec-Fetch-Site: cross-site` request → 403; `/healthz` and
  `/orchestrator/api/*` behave as before.

### 2. Stream Opus

- `POST /console/api/chat` in `console/server.ts` (or a module it mounts): `streamText` with `@ai-sdk/anthropic`; model
  `SUPERAGENT_CONSOLE_MODEL`, defaulting to the current Opus model id named in
  the `@ai-sdk/anthropic` docs (pin it; note the source in the commit).
- System prompt in `console/prompts/system.md`, read at start: what the
  deployment is (Superagent on DSH, the Delivery gates, `uat-test`,
  `claude-review`), what the console may do (read, explain, draft) and the issue
  format from `templates/AGENTS.md` "Writing issues".
- `compose.yaml`: `console` gets `ANTHROPIC_API_KEY` and
  `SUPERAGENT_CONSOLE_MODEL` only. With no key (a deployment on
  `CLAUDE_CODE_OAUTH_TOKEN` alone), the route answers 503 `not-configured` and
  the page shows "Chat is not configured: set ANTHROPIC_API_KEY".
- **Tests** (vitest): the route with the AI SDK mock model streams its text;
  missing key → 503 and no provider call.
- **Done when:** locally, with a key, replies stream into the Thread.

### 3. Doorway plugin

- `dsh/console-plugin/package.json` (`name: superagent-console`,
  `dsh.client.platform: web`, `dsh.client.inject` with the client packages it
  uses, `exports["./client"]: ./client.js`, `dsh.manifestVersion: 1`) and a
  hand-written `client.js` in the loader wrapper (`React.createElement`, no
  build step). `apply` registers a `main` panel and a `sidebar.panellist` icon
  the way the prototype does; the panel is a full-size
  `<iframe src="/console/" title="Console">`.
- `Dockerfile`: copy it to `/opt/superagent-console` and add
  `dsh plugin --profile superagent add 'link:/opt/superagent-console'` next to
  the existing `add` line. Keep the COPY block of `scripts/` untouched (the F1
  branches edit it).
- **Tests:** `test_console_plugin.py` runs `client.js` under Node with a fake
  `__ModuleLoader__`, fake `require` and fake `ctx`, and asserts one `main` and
  one `sidebar.panellist` registration whose body renders the iframe; a Docker
  test that `dsh --profile superagent --dump-config` lists `superagent-console`.
- **Done when:** in a local `docker compose up`, the icon opens the chat inside
  the panel, and the Superagent panel still loads.

### 4. Read and draft tools

- Verify first: from the `console` container, a GET to
  `http://dashboard:8080/orchestrator/api/state` with a valid cookie answers 200.
  If DSH's check on `/` rejects the internal Host, forward the public Host
  (`SERVICE_FQDN_DASHBOARD`) and record why in the client.
- `console/lib/orchestrator.ts`: base URL `SUPERAGENT_CONSOLE_API_URL` (default
  `http://dashboard:8080/orchestrator/api`), forwards the incoming `cookie`
  header, sends GET only and throws on any other method.
- Tools (AI SDK `tool()`): `list_tickets`, `get_ticket`, `get_settings` (values of
  keys matching token, key, secret or password redacted), `get_prompts`,
  `draft_issue` (returns Markdown in the issue format; writes nothing). No
  GitHub client in the service.
- **Tests:** each tool against a mocked `fetch`; the client refuses non-GET;
  redaction; the cookie is forwarded and never logged.
- **Done when:** asked "what is ticket X waiting for", Opus answers from the
  live API on a local deployment.

### 5. Keep threads and restore consistency

- Threads persist in the browser's localStorage (assistant-ui's local thread
  storage if it has one, else a small adapter); nothing stored server-side.
- README section "Console": what it is, how to open it, `ANTHROPIC_API_KEY`
  requirement, trust (cookie gate, GET-only, drafts only, threads in the
  browser). `.env.example`: `SUPERAGENT_CONSOLE_MODEL`.
- **Done when:** a reload keeps the thread; README, `.env.example`,
  `compose.yaml` and the tests agree; the PR body (use the `pr` skill) lists the
  verification below with evidence.

## Verify on `attesta` after the merge

Signed-in browser: the icon opens the console, Opus answers about a live ticket,
and a drafted issue matches the template. Signed-out `curl
https://<domain>/console/` returns 401. With `ANTHROPIC_API_KEY` unset the page
says it is not configured. Workers, review and UAT run as before.

## Running alongside the other work

- **Testable from the first push:** CI runs every step on the draft PR; a local
  `docker compose up` (with `.env`) shows the panel. The live deployment shows
  it only after the merge, since Coolify deploys `attesta` alone. The squash
  merge reverts as one commit.
- **Files:** the F1 branches (`f1/*`, PR #19) change `scripts/`, `uat/tests/test_gate.py`,
  `uat/tests/test_checks.py`, the `Dockerfile` COPY block and later the prompts
  and README sections 4–5. This PR touches none of those lines; expect at most a
  trivial `Dockerfile` or README rebase.
- **Runtime:** workers, `claude-review` and `uat-gate` call nothing new and
  receive nothing new. The console shares the reviewer's Anthropic key, so its
  spend and rate limits share that key's.

## Risks

- A throwing doorway `client.js` could break the DSH web UI; step 3's local check
  and the Node test cover it.
- The prototype is built against 0.2.1-era client packages; inject only what
  `apply` uses.
- assistant-ui, the AI SDK and Vite move fast; pin exact versions in
  `console/package.json` and commit the lockfile.
