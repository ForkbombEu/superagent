"""Parse the `## User Acceptance Tests` section of a GitHub issue body.

Parser rules (templates/AGENTS.md documents them for issue authors):

- The section starts at a line that is exactly `## User Acceptance Tests`
  (case-sensitive, trailing spaces ignored) outside fenced code blocks, and ends
  at the next level-1 or level-2 heading outside fenced code blocks, or at the
  end of the body.
- No such line: UAT is NOT_REQUIRED. A level-2 heading that differs from it only
  in case or plural ("## User acceptance test") is BLOCKED rather than silently
  ignored, as is more than one section.
- Before the first scenario, one optional `Environment: <name>` line selects the
  environment; other text there is a preamble given to the tester as context.
- Scenarios are `### UAT-<n>: <title>` headings with a positive integer n,
  each followed by free-form, non-empty steps (Given/When/Then recommended).
  Any other `### ` heading in the section, a duplicate id, an empty scenario
  or a section without scenarios is BLOCKED.
- `Depends on #<a>, #<b>` lines are issue metadata, not tests: they are left
  out of the section, so dependency processing and the spec hash ignore them.

The spec hash covers the canonical form: line endings normalized, trailing
spaces and leading/trailing blank lines of each part removed. Whitespace-only
edits keep the hash; any other edit, or another environment, changes it.
"""
import hashlib
import json
import re
from dataclasses import dataclass

HEADING = '## User Acceptance Tests'
NOT_REQUIRED, BLOCKED, READY = 'NOT_REQUIRED', 'BLOCKED', 'READY'
MAX_SECTION_CHARS = 32768
MAX_SCENARIOS = 50

SCENARIO = re.compile(r'### (UAT-[1-9][0-9]{0,3}):[ \t]*(\S.*)')
ENVIRONMENT = re.compile(r'Environment:[ \t]*(.*)')
ENVIRONMENT_NAME = re.compile(r'[a-z][a-z0-9-]{0,31}')
DEPENDS = re.compile(r'Depends on #\d+(?:[ \t]*,[ \t]*#\d+)*')
FENCE = re.compile(r' {0,3}(```|~~~)')
TOP_HEADING = re.compile(r' {0,3}#{1,2}(?:[ \t]|$)')
NEAR_MISS = re.compile(r' {0,3}## +user +acceptance +tests? *', re.IGNORECASE)


@dataclass(frozen=True)
class Scenario:
    id: str
    title: str
    steps: str


@dataclass(frozen=True)
class Spec:
    status: str
    reason: str = ''
    environment: str | None = None
    preamble: str = ''
    scenarios: tuple[Scenario, ...] = ()


def _outside_fences(lines):
    """Yield (index, line, inside_fence) for each line, tracking ``` and ~~~ fences."""
    fence = None
    for index, line in enumerate(lines):
        match = FENCE.match(line)
        if match and (fence is None or match.group(1) == fence):
            fence = match.group(1) if fence is None else None
            yield index, line, True
            continue
        yield index, line, fence is not None


def _tidy(lines):
    text = '\n'.join(line.rstrip() for line in lines)
    return text.strip('\n')


def parse(body):
    """Return the Spec of an issue body (None or '' counts as an empty body)."""
    lines = (body or '').replace('\r\n', '\n').replace('\r', '\n').split('\n')
    starts, tops, near = [], [], []
    for index, line, fenced in _outside_fences(lines):
        if fenced:
            continue
        if line.rstrip() == HEADING:
            starts.append(index)
        elif NEAR_MISS.fullmatch(line.rstrip()):
            near.append(line.strip())
        if TOP_HEADING.match(line):
            tops.append(index)
    if not starts:
        if near:
            return Spec(BLOCKED, f'heading {near[0]!r} must be written exactly {HEADING!r}')
        return Spec(NOT_REQUIRED, 'the issue has no User Acceptance Tests section')
    if len(starts) > 1:
        return Spec(BLOCKED, f'the issue has {len(starts)} {HEADING!r} sections; keep one')
    start = starts[0]
    end = next((index for index in tops if index > start), len(lines))
    section = lines[start + 1:end]
    if sum(len(line) + 1 for line in section) > MAX_SECTION_CHARS:
        return Spec(BLOCKED, f'the UAT section is longer than {MAX_SECTION_CHARS} characters')

    environment, preamble, scenarios = None, [], []
    for _, line, fenced in _outside_fences(section):
        stripped = line.strip()
        if not fenced and line.startswith('### '):
            match = SCENARIO.fullmatch(line.rstrip())
            if not match:
                return Spec(BLOCKED, f'heading {stripped!r} is not a scenario: write "### UAT-<n>: <title>"')
            scenarios.append((match.group(1), match.group(2).strip(), []))
            continue
        if not fenced and DEPENDS.fullmatch(stripped):
            continue
        if scenarios:
            scenarios[-1][2].append(line)
            continue
        environment_line = None if fenced else ENVIRONMENT.fullmatch(stripped)
        if environment_line:
            name = environment_line.group(1).strip()
            if environment is not None:
                return Spec(BLOCKED, 'the UAT section has more than one Environment line')
            if not ENVIRONMENT_NAME.fullmatch(name):
                return Spec(BLOCKED, f'Environment {name!r} is not a valid environment name')
            environment = name
            continue
        preamble.append(line)

    if not scenarios:
        return Spec(BLOCKED, 'the UAT section has no "### UAT-<n>: <title>" scenarios')
    if len(scenarios) > MAX_SCENARIOS:
        return Spec(BLOCKED, f'the UAT section has more than {MAX_SCENARIOS} scenarios')
    ids = [scenario_id for scenario_id, _, _ in scenarios]
    duplicates = sorted({scenario_id for scenario_id in ids if ids.count(scenario_id) > 1})
    if duplicates:
        return Spec(BLOCKED, f'duplicate scenario ids: {", ".join(duplicates)}')
    tidy = tuple(Scenario(scenario_id, title, _tidy(steps)) for scenario_id, title, steps in scenarios)
    empty = [scenario.id for scenario in tidy if not scenario.steps]
    if empty:
        return Spec(BLOCKED, f'scenarios without steps: {", ".join(empty)}')
    return Spec(READY, environment=environment, preamble=_tidy(preamble), scenarios=tidy)


def canonical(spec, environment):
    """The canonical, hashed form of a READY spec run in the resolved environment."""
    if spec.status != READY:
        raise ValueError(f'only a READY spec has a canonical form, not {spec.status}')
    return {
        'version': 1,
        'environment': environment,
        'preamble': spec.preamble,
        'scenarios': [{'id': s.id, 'title': s.title, 'steps': s.steps} for s in spec.scenarios],
    }


def spec_hash(spec, environment):
    """Full SHA-256 of the canonical JSON form (sorted keys, no spaces, UTF-8)."""
    data = json.dumps(canonical(spec, environment), sort_keys=True, separators=(',', ':'), ensure_ascii=False)
    return hashlib.sha256(data.encode('utf-8')).hexdigest()


def from_canonical(data):
    """The READY spec of a canonical form (the snapshot the controller passes between jobs)."""
    return Spec(READY, environment=data['environment'], preamble=data['preamble'],
                scenarios=tuple(Scenario(s['id'], s['title'], s['steps']) for s in data['scenarios']))
