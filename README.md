# DSH Superagent on Coolify with Caddy HTTP Basic Authentication

> Unofficial integration, **not end-to-end tested in Coolify**. This specifically
> targets Coolify's **Caddy** reverse proxy. Traefik labels are different.

## Architecture

```
Browser (HTTPS)
  -> Coolify Caddy proxy :443 [HTTP Basic Auth, caddy_0.basicauth...]
    -> dashboard (internal Caddy :8080)
       -> superagent:3083 (socat tunnel to DSH 127.0.0.1:3082)
```

The inner proxy is necessary because the Superagent management API requires
`Host: 127.0.0.1:3082` or `localhost:3082`, and an external browser `Origin`
would otherwise return 403. Only management API traffic receives the adjusted
headers. Other DSH UI traffic preserves the public Host, which `--trusted-host`
allows. The external Basic Auth layer must cover **all** paths, including
`/orchestrator/api/*` and WebSocket connections.

## 1. Configure Coolify

Create a Git-backed Docker Compose Application referencing `compose.yaml`.
Keep Compose deployment in **normal/managed mode**, not Raw, so Coolify can
connect the dashboard service to its proxy and generate the Caddy route.

In Coolify **Domains**, create an HTTPS domain, e.g. `superagent.example.com`,
assign it to the **`dashboard`** service on **internal port `8080`**.
Do **not** attach the domain to `superagent`.

Configure Environment Variables:

| Variable | Description |
| --- | --- |
| `DEEPSEEK_API_KEY` | Model provider secret (runtime only) |
| `GH_TOKEN` | GitHub classic PAT for dedicated agent account (runtime only) |
| `GITHUB_REPOSITORY` | e.g. `ForkbombEu/credimi-attestbook` |
| `SUPERAGENT_DOMAIN` | Public hostname only, e.g. `superagent.example.com` (no https://, no path) |
| `BASIC_AUTH_USERNAME` | HTTP Basic Auth username, e.g. `admin` |
| `BASIC_AUTH_PASSWORD_HASH` | **bcrypt hash** (NOT a plaintext password) |

Create a bcrypt hash locally (this prints only the hash, not the plaintext):

```bash
docker run --rm -it caddy:2.10-alpine caddy hash-password
```

Enter a long random password at the hidden prompt. Copy the resulting
`$2a$...` or `$2b$...` string into
`BASIC_AUTH_PASSWORD_HASH` in Coolify. Keep the dollar characters unchanged.
The `compose.yaml` label references `${BASIC_AUTH_PASSWORD_HASH:?}` so the
hash is inserted by Compose interpolation, not embedded in Git. If entering a
literal hash directly in Compose labels instead, escape each `$` as `$$`.

The Basic Auth label on the **dashboard** service is:

```yaml
labels:
  - "caddy_0.basicauth.${BASIC_AUTH_USERNAME:-admin}=${BASIC_AUTH_PASSWORD_HASH:?}"
```

`caddy_0` is the **first** Coolify-generated Caddy domain. If the dashboard
has multiple domains or the generated label starts `caddy_1=`, match that
index instead. The label must be on the same container as the corresponding
route; check the deployed labels in Coolify after saving.

**Security**: Basic Auth is one shared credential, without MFA or individual
accounts. Use HTTPS, a strong random password, and preferably a private access
policy or IP allowlist as a second layer. The DSH workers execute code and
have repository credentials. Do not directly publish 3082, 3083 or 8080.

## 2. Deploy, validate and open

Deploy the app from Coolify. When Caddy routes are ready:

```bash
# Must be HTTP 401 before authentication:
curl -I https://superagent.example.com/

# Basic Auth must then pass; the DSH GUI may still require its own
# launch-token / session-cookie flow:
curl -I -u 'admin:YOUR_LONG_RANDOM_PASSWORD' https://superagent.example.com/
```

Open `https://superagent.example.com/`, pass browser Basic Auth, and follow
DSH's Web launch-token flow if prompted. The UI uses its own authenticated
session separately from proxy Basic Auth. To inspect Superagent API errors,
use the browser developer tools; `/orchestrator/api/state` should not fail
with DSH's `Same-origin management request required` response after login.

Check the **actual generated Caddy labels** under the dashboard service;
Coolify's Caddy route must include the `basicauth` label and target port 8080.
If the domain isn't accessible, verify the Coolify Caddy proxy and stack's
network/route wiring before changing the DSH app.

## 3. One-time Superagent configuration

Runtime env vars do not configure Superagent's internal GitHub Source, Policy
allowlist, or Executor. In the DSH panel, configure:

- Source GitHub repository and `/usr/local/bin/gh-agent`.
- Eval > GitHub allowed users (your own GitHub account).
- Executor directory `/home/agent/.dsh/state/superagent/work`,
  prepare command `/usr/local/bin/prepare-worktree.sh` and worker concurrency.
- Prompts and a review policy appropriate for your project.

Default proactive accept mode `observe` still accepts explicit requests from
allowlisted users; don't enable automatic inferred work prematurely.

## 4. Operational notes and limitations

- Keep existing service name `superagent` and volume `superagent_home` when
  migrating from the previous package. Docker Compose/Coolify may still
  rename/prefix volume names; **confirm the deployed volume before migration**.
- `superagent:3083` exists on the internal Compose network only. The outer
  Coolify Caddy proxy should route only to `dashboard:8080`.
- DSH itself uses `--public-url` and `--trusted-host` to support the external
  URL. If your pinned DSH CLI version does not recognize those flags, you may
  need to upgrade its version rather than removing the host security check.
- The management API's loopback check is intentionally rewritten by the inner
  proxy. This is safe **only if every external access path is authenticated**.
  A service connected to the same internal Docker network could otherwise
  bypass the outer Basic Auth and access the management API; isolate the
  deployment from untrusted containers.
- The image build caches npm/pnpm downloads in BuildKit cache mounts on the
  build server. Keep **Advanced > Build cache** on **Use Docker build cache**:
  "Rebuild from scratch" / "Force deploy (without cache)" skip both the layer
  cache and these mounts. Unchanged layers (apt, `dsh` install) are reused
  entirely; the cache mounts speed up the `dsh` install when its layer reruns.
  Because the upstream clone layer is cached too, use a forced rebuild to pick
  up new `dsh-superagent-prototype` commits on `main`.
- No real Docker build or live Coolify deployment was available in this
  environment; validate the deployed generated Caddy labels and auth behavior.

References:
- https://coolify.io/docs/core/networking/proxy/caddy/basic-auth
- https://coolify.io/docs/applications/builds/docker-compose
- https://github.com/deepseek-ai/dsh-superagent-prototype/blob/main/src/app/http.ts
- https://github.com/deepseek-ai/deepseek-harness/blob/master/docs/user/guide/public-deployments.md
