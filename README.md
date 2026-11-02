# DeepSeek Superagent on Coolify (Docker Compose)

**Unofficial packaging; not validated with a real Docker build.** Built around
[the prototype deployment guide](https://github.com/deepseek-ai/dsh-superagent-prototype/blob/main/docs/006-deployment.md)
and [Coolify's Compose build](https://coolify.io/docs/applications/builds/docker-compose).

## Deploy using Coolify

1. Put these files in a Git repository, preserving the `scripts/` directory.
   In Coolify select **New > Git repository > Compose**. Use `compose.yaml`
   as the Compose location (root base directory).
2. In **Environment Variables**, set the following **runtime variables**:

   | Variable | Required | Purpose |
   | --- | --- | --- |
   | `DEEPSEEK_API_KEY` | Yes | The DeepSeek Harness model provider key |
   | `GH_TOKEN` | Yes | Classic PAT belonging to a **dedicated GitHub agent account** |
   | `GITHUB_REPOSITORY` | Yes | Repository to clone (`OWNER/REPO`) |
   | `SUPERAGENT_HOST_PORT` | No | Loopback-only port on the Coolify host, defaults to 3082 |

   Mark `DEEPSEEK_API_KEY` and `GH_TOKEN` as **secret**. Do not set them as
   Docker build arguments or commit them into `compose.yaml` or the Dockerfile.
   Coolify sees `${VAR:?}` as required and prevents deploying without them.
3. Give the agent GitHub account **write access** to `OWNER/REPO` and configure
   its **notifications** for that repository. Use a **classic** personal access
   token, because GitHub's notifications endpoint does not support fine-grained
   tokens. For private repositories, use the classic `repo` scope. For a public
   repository, `public_repo` plus `notifications` can be enough; validate
   the account's ability to read notifications, create PRs and push branches.
   Use the narrowest scopes compatible with your repository.
4. Deploy. The entrypoint authenticates the `gh-agent` wrapper via a temporary
   (tmpfs) token file, verifies the token, clones the repo to
   `/home/agent/src/<REPO>` when missing, and starts `dsh --profile superagent`.
   The persistent named volume keeps the agent profile, SQLite state, and worktrees.

## Open the dashboard securely

**Do not attach a public domain to this service in Coolify.** The DeepSeek
Harness Superagent management API expects a localhost Host header, and the UI
can change execution commands. A public reverse proxy would be both unsafe
and incompatible without special handling.

Connect to the Coolify server from your computer:

```sh
ssh -N -L 3082:127.0.0.1:3082 YOUR_USER@YOUR_COOLIFY_SERVER
```

If you configured `SUPERAGENT_HOST_PORT`, replace the **right-hand** 3082 with
that value. Then open **http://127.0.0.1:3082** on your computer and select
**Superagent**. This is the actual Web dashboard, not an external route.

## One-time Superagent configuration in its dashboard

The runtime keys are injected automatically, but **Superagent's task routing
and authorization are NOT configured through those environment variables**.
In Superagent settings:

1. **Sources > GitHub:** set `OWNER/REPO` and the CLI path
   `/usr/local/bin/gh-agent`. The GitHub PAT must belong to the agent user.
2. **Policy:** add the GitHub username(s) allowed to assign work. Leave
   proactive/inferred mode at `observe` until you are confident in its behavior.
3. **Execution:** set directory root to
   `/home/agent/.dsh/state/superagent/work` and prepare command to
   `/usr/local/bin/prepare-worktree.sh`. Set max concurrent workers deliberately.
4. **Models:** select a DeepSeek model. The `DEEPSEEK_API_KEY` inherited by the
   DSH process is used by the built-in DeepSeek provider. Saving a second API
   key in DSH is unnecessary if the provider is using the environment fallback.
5. **Prompts:** configure the commit author identity and safe merge/review policy.

An authorized GitHub user can then create a new Issue comment mentioning
`@YOUR_AGENT_GITHUB_USERNAME` and assigning a task. The agent polls GitHub and
starts its own worker in the container.

## Why a GitHub wrapper, not just a GH_TOKEN env variable?

Superagent's setup and worker subprocesses intentionally remove env vars whose
names contain `TOKEN`, `KEY`, `PASSWORD`, or `SECRET`; its generated git config
also drops `GH_TOKEN`/`GITHUB_TOKEN`. This deployment handles the difference:

- `DEEPSEEK_API_KEY` is passed to the main DSH model provider as an environment
  variable, as supported by DeepSeek Harness.
- At startup, `GH_TOKEN` is copied into `/run/superagent-secrets/github-token`
  (tmpfs), and removed from the **DSH launch shell** environment.
- `/usr/local/bin/gh-agent` reads that file each time GitHub CLI is invoked,
  then sets `GH_TOKEN` only for that GitHub CLI process. This works even when
  Superagent launches the wrapper from a sanitized subprocess.

No interactive `gh auth login` is necessary. Updating `GH_TOKEN` in Coolify
requires recreating/restarting the container to refresh the ephemeral file.

**Security note:** Workers have broad shell permissions within this container,
so they can access this token file if malicious or compromised. Give the GitHub
account access only to the target repository, and NEVER mount the Docker socket,
host home directory, personal SSH keys, or unrelated credentials. Coolify/Docker
administrators with container access can inspect runtime secrets; this setup is
not protection against a malicious host administrator.

## Operations

Check the runtime logs in Coolify. To verify agent authentication without
printing secrets, use Coolify's container terminal:

```sh
/usr/local/bin/gh-agent api user --jq .login
/usr/local/bin/gh-agent auth status
```

To check Superagent's internal API from inside its container:

```sh
curl -fsS -H 'x-orchestrator-request: 1' \
  http://127.0.0.1:3082/orchestrator/api/state
```

**Limitations:** This targets one repository per deployment; the agent account
must receive GitHub notifications. The prototype is research code, not
production-hardened. Upgrading Superagent/DHS may require reviewing persistent
profile state. The Dockerfile, Compose, and scripts have been statically
checked, but an end-to-end build and deployment have not been run here.
