# Package shape for `superagent init`

**Status:** accepted (F11.1)

Adopting Superagent on a product repository today means hand-copying
`templates/AGENTS.md`, planning skills, and inventing `.superagent/uat.yaml`.
F11 needs one command that scaffolds those **target-owned** files while
runners and gates stay **deployment-owned**. This ADR names that ownership
split and the single packaging mechanism F11.2 (`init`) and F11.3 (`upgrade`)
must follow.

## Decision

**Ship `init` / `upgrade` as subcommands of the existing in-repo Python CLI**
(`scripts/superagent`, already installed to `/usr/local/bin/superagent` in the
image). Templates stay the Superagent repo’s `templates/` tree; the CLI copies
from that tree (checkout or image-baked copy) into a caller-chosen target
directory. Do **not** introduce a separate npm/pip package for F11.2/F11.3
unless the fallback below is triggered.

### Target-owned paths (written by `init` / `upgrade`)

| Path | Role |
| ---- | ---- |
| `AGENTS.md` | Base agent instructions for the product repo |
| `.superagent/uat.yaml` | PR-controlled UAT sandbox config (schema in `uat/config.schema.json`) |
| `.agents/skills/**` | Planning skills (`write-issues`, `architect`) loaded by DSH/Cursor |
| `.superagent/UPGRADE.md` | After `upgrade` only: human/agent work order of what changed |

These files are committed in the **target** repository and may appear on pull
requests. They must never configure reviewers, testers, models, secrets, or
gate policy.

### Deployment-owned surface (never scaffolded into the target)

| Surface | Role |
| ------- | ---- |
| `superagent.yaml` / schema / rendered profile | Tiers and Roles; never read from a PR (`GLOSSARY.md`) |
| Runners and gates | `superagent check` / `merge`, `uat-test`, `claude-review`, `uat-gate` / `uat-runner` |
| Coolify compose, image entrypoint, secrets | One deployment ↔ one `GITHUB_REPOSITORY` |
| Delivery prompts, role markdown, managed Claude settings | Image `/usr/local/share/superagent/` and related paths |

### Packaging / delivery mechanism

1. Extend `scripts/superagent` with `init` and (later) `upgrade` (argv dispatch
   today, or `argparse` if the surface grows — same pattern as
   `superagent_config.py`’s subcommands).
2. Resolve the template root the same way deployment config resolves layout:
   - **Dev / clone:** repo-root `templates/` next to `scripts/`.
   - **Image:** bake `templates/` into the image (e.g. `COPY templates/
     /usr/local/share/superagent/templates/`) so `/usr/local/bin/superagent`
     can copy without a git checkout of this repo. Today the Dockerfile copies
     scripts and deployment config but **not** `templates/` — F11.2 must add
     that COPY (or an equivalent install step).
3. Highest test seam: filesystem writes into a temporary directory (no Docker
   required for unit coverage of copy / refuse-overwrite / `--force`).

One mechanism for both commands: same binary, same template root, same
ownership rules. `upgrade` refreshes skills (and related template files) from
that root and writes `.superagent/UPGRADE.md`; it does not pull runners into
the target.

## Evidence

- **CLI already ships in-image.** `Dockerfile` copies `scripts/superagent` (and
  sibling gates) to `/usr/local/bin/`; there is no root `package.json` or
  publishable CLI package in this repository.
- **Templates are git source of truth, manual today.** README “Target
  repository setup” / “Planning side” instruct copying `templates/AGENTS.md`
  and `templates/.agents/skills/` by hand. F9 landed those skills under
  `templates/`; no `templates/.superagent/uat.yaml` file yet (inline sample in
  `templates/AGENTS.md`; schema + fixture under `uat/`).
- **Ownership already named.** `GLOSSARY.md`: `superagent.yaml` is
  deployment-owned; `.superagent/uat.yaml` is PR-controlled. `docs/extending-superagent.md`
  §2: `.superagent/` is the installable target surface; workers/testers/runners
  stay out of the target repo. Issue #59 / plan F11: runners stay in the
  deployment.
- **CMS/`npx` path is aspirational, not current.** `docs/extending-superagent.md`
  §5 *infers* `npm i superagent` and `npx upgrade-superagent` from EmDash; that
  is a later embedding story (console in host app), deferred after F11 in the
  feature plan. Preferring a package now would invent a distribution channel
  the repo does not use for any other gate.

## Fallback

If the in-image / in-repo CLI proves insufficient for the primary adopter
path — specifically: maintainers must scaffold or upgrade **without** a
checkout of this Superagent repo and **without** access to the Coolify image’s
`/usr/local/bin/superagent` — publish a thin installable package (npm or pip)
that embeds the same `templates/` tree and the same copy/`upgrade` logic, and
keep the Dockerfile CLI as a thin wrapper or identical entrypoint. Trigger
criteria: F11.2/F11.3 cannot be documented as a one-liner for laptop use, or
`upgrade` needs independent semver from the deployment image tag. Until then,
do not split packaging.

## Consequences for F11.2 / F11.3

- F11.2: implement `superagent init <dir>` against `templates/`; add image
  `COPY` (or install) of that tree; refuse overwrite unless forced; temp-dir
  tests. Add a sample `.superagent/uat.yaml` under `templates/` (derive from
  `uat/tests/fixtures` / schema) so init does not invent schema.
- F11.3: implement `superagent upgrade` on the same binary and template root;
  write `.superagent/UPGRADE.md`; still no runners in the target package.
- Do not change Coolify deploy, worker runtime, or mount console into target
  apps as part of F11.
