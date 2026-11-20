# Superagent

Language for the deployment that runs workers, the reviewer and UAT against a
target repository.

## Language

**Tier**:
A named cost and capability band (high, mid, low) that a Role selects instead of a concrete model.
_Avoid_: model class, model level, effort band

**Role**:
A job the deployment runs (implementer, reviewer, uat-tester, and worker subagent presets such as explorer).
_Avoid_: agent type, persona, preset (a preset is the DSH packaging of a Role)

**superagent.yaml**:
The deployment-owned file that declares Tiers and Roles; it ships in the image or the deployment and is never read from a pull request.
_Avoid_: roles file, agent config, .superagent/uat.yaml (that file is PR-controlled UAT sandbox config)

**Env override**:
An environment variable that replaces the corresponding value from superagent.yaml; when set, the environment wins.
_Avoid_: fallback, default
