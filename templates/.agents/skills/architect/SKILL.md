---
name: architect
description: >
  Map modules and seams, query the code graph when available, and record ADRs
  for a Superagent target repository. Use when designing architecture, building
  a module map, tracing callers or dependents, deciding where a seam goes, or
  writing an ADR before implementation issues.
---

# Architect

Build a high-level view of how modules connect, so planning issues land on
the right seams. The repository's `docs/specification.md` and any
`GLOSSARY.md` / `docs/adr/` are the domain sources of truth; this skill is the
procedure that turns them (and the code) into a **module map** and, when
warranted, ADRs.

Planning side, high tier: a person stays in the loop. Delivery still starts
from issues — hand off to the write-issues skill once the map and decisions
are stable enough to slice.

## 1. Orient

1. Read `docs/specification.md` (or the plan the person named) and
   `AGENTS.md` Project constraints.
2. Load domain language: `GLOSSARY.md` at the root, or each glossary named by
   `GLOSSARY-MAP.md` when the repo has multiple contexts. Create nothing yet.
3. Scan existing ADRs under `docs/adr/` (and context-local `docs/adr/` if any).
4. Discover the structural toolkit:
   - Prefer **codebase-memory-mcp** when its tools are available
     (`list_projects`, `get_architecture`, `search_graph`, `trace_path`,
     `query_graph`, `get_code_snippet`, `manage_adr`, `check_index_coverage`).
   - Otherwise use ordinary search and file reads — still map structure, just
     slower.

Done when the ask, the domain vocabulary, existing ADRs, and the chosen
toolkit (or grep/read fallback) are named in one short line each.

## 2. Module map

Produce a **module map**: the modules that matter for the ask, how they
connect, and where new work should sit.

Use these terms exactly:

| Term | Meaning |
| ---- | ------- |
| **Module** | Anything with an interface and an implementation (function, package, or tier-spanning slice). |
| **Interface** | Everything a caller must know: signature, invariants, ordering, errors, config, performance. |
| **Seam** | Where that interface lives — the place you can alter behaviour without editing callers. |
| **Depth** | Much behaviour behind a small interface. Prefer deep modules; avoid shallow pass-throughs. |

With the code graph (when available):

1. `list_projects` / `index_status` — confirm the project is indexed and fresh.
2. `get_architecture` — packages, entry points, layers, cycles, hotspots for
   the area in scope.
3. `search_graph` — find symbols by name; then `trace_path` (inbound /
   outbound / both) for callers and dependents.
4. `query_graph` — multi-hop or cross-service edges when a single trace is not
   enough. Prefer graph answers over reading whole files.
5. `get_code_snippet` for material claims; after candidate paths are known,
   `check_index_coverage` once with those paths before trusting absence or
   exhaustiveness.

Without the graph: walk the directory layout and imports for the same
outcomes — modules, call direction, and the home for the change.

Default: **extend or deepen an existing module** that already earns its keep
(deletion test: removing it would re-scatter complexity across callers).
Propose a **new** module only when no existing home passes that test.

Present the map in chat (no HTML file unless the person asks):

- **Explored with:** `<toolkit>` or `grep/read`
- For each module in scope: **name** (domain term when the glossary has one),
  **responsibility** (one line), **interface** sketch, **seam**, **neighbours**
  (who calls / what it calls)
- **Fit for this ask:** which module to extend, or why a new seam
- **Out of scope:** modules deliberately left alone
- **ADR conflicts:** only if an existing ADR blocks the fit; one line each

Done when the person can point at the home module (or approve a new seam)
and the neighbour list matches what the toolkit showed.

## 3. ADRs

Offer an ADR only when all three are true:

1. **Hard to reverse** — changing mind later is costly
2. **Surprising without context** — a future reader would wonder why
3. **Real trade-off** — genuine alternatives were considered

Skip reversible, obvious, or alternative-free choices. Create `docs/adr/`
lazily on the first ADR. Number sequentially (`0001-slug.md`, …): scan for
the highest existing number and increment.

Minimal body (a paragraph is enough):

```markdown
# {Short title}

{1–3 sentences: context, decision, why.}
```

Optional only when they earn their load: Status, Considered Options,
Consequences. When codebase-memory-mcp's `manage_adr` is available, use it to
outline or update the same files under `docs/adr/`.

Update `GLOSSARY.md` inline when a domain term is resolved during the
session; keep it free of implementation detail.

Done when every accepted hard decision has an ADR on disk (or the person
declined), and glossary edits for new terms are committed to the working tree.

## 4. Hand off to issues

When the map (and any ADRs) are stable enough to slice work, follow
[`.agents/skills/write-issues/SKILL.md`](../write-issues/SKILL.md): one design
issue may land the ADR first (`Model :: high`, Effort up to ~200k); then
implementation issues depend on it.

Done when the person has either a confirmed module map ready for write-issues,
or an explicit stop (map only / ADR only).
