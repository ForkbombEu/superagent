"""Load and resolve deployment-owned superagent.yaml.

Validates against superagent.schema.json, resolves Role tiers into concrete
models, then applies environment overrides (env wins). Callers must pass an
absolute path under the deployment or image — never a pull-request checkout.
"""
from __future__ import annotations

import copy
import json
import os
from dataclasses import dataclass, field
from typing import Any, Mapping

import jsonschema
import yaml

DIRECTORY = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(DIRECTORY)
SCHEMA_PATH = os.path.join(ROOT, 'superagent.schema.json')
# Image path F6.2/F6.3 will use; F6.1 only documents it.
DEPLOYMENT_CONFIG_PATH = '/usr/local/share/superagent/superagent.yaml'


class ValidationError(Exception):
    """The file or environment overrides are not a valid configuration."""


@dataclass
class ResolvedConfig:
    """Concrete models and role surfaces after tier resolution and env overrides."""

    version: int
    tiers: dict[str, dict[str, Any]]
    roles: dict[str, dict[str, Any]]
    executor: dict[str, str] | None = None
    reviewer: dict[str, Any] = field(default_factory=dict)
    uat: dict[str, str] | None = None
    tier_routes: list[dict[str, str]] = field(default_factory=list)


def _schema() -> dict:
    with open(SCHEMA_PATH, encoding='utf-8') as file:
        return json.load(file)


def _require_absolute_deployment_path(path: str) -> str:
    if not os.path.isabs(path):
        raise ValidationError(
            f'superagent config path must be absolute (deployment/image), got {path!r}'
        )
    return path


def _load_yaml(path: str) -> Any:
    try:
        with open(path, encoding='utf-8') as file:
            return yaml.safe_load(file)
    except OSError as error:
        raise ValidationError(f'cannot read {path}: {error}') from error
    except yaml.YAMLError as error:
        raise ValidationError(f'invalid YAML in {path}: {error}') from error


def _validate_document(document: Any) -> dict:
    if not isinstance(document, dict):
        raise ValidationError('superagent.yaml must be a mapping')
    try:
        jsonschema.validate(document, _schema())
    except jsonschema.ValidationError as error:
        where = '/'.join(str(part) for part in error.absolute_path) or '(root)'
        raise ValidationError(f'{where}: {error.message}') from error
    return document


def _dsh_dict(tier: Mapping[str, Any]) -> dict[str, str] | None:
    dsh = tier.get('dsh')
    if not dsh:
        return None
    out = {'provider': dsh['provider'], 'model': dsh['model']}
    if dsh.get('reasoningEffort'):
        out['reasoningEffort'] = dsh['reasoningEffort']
    return out


def _resolve_tiers(raw_tiers: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    resolved: dict[str, dict[str, Any]] = {}
    for name, tier in raw_tiers.items():
        entry: dict[str, Any] = {}
        if 'claude' in tier:
            entry['claude'] = tier['claude']
        dsh = _dsh_dict(tier)
        if dsh:
            entry['dsh'] = dsh
        resolved[name] = entry
    return resolved


def _tier_or_raise(tiers: Mapping[str, Any], name: str, where: str) -> dict[str, Any]:
    if name not in tiers:
        raise ValidationError(f'{where}: unknown tier {name!r}')
    return tiers[name]


def _resolve_roles(raw_roles: Mapping[str, Any], tiers: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    roles: dict[str, dict[str, Any]] = {}
    for name, role in raw_roles.items():
        tier_name = role['tier']
        tier = _tier_or_raise(tiers, tier_name, f'roles.{name}')
        resolved: dict[str, Any] = {
            'runtime': role['runtime'],
            'tier': tier_name,
        }
        if 'instructions' in role:
            resolved['instructions'] = role['instructions']
        if 'sources' in role:
            resolved['sources'] = list(role['sources'])

        if role['runtime'] in ('dsh-worker', 'dsh-headless'):
            dsh = tier.get('dsh')
            if not dsh:
                raise ValidationError(f'roles.{name}: tier {tier_name!r} has no dsh model')
            resolved['model'] = copy.deepcopy(dsh)
        elif role['runtime'] == 'claude-code':
            claude = tier.get('claude')
            if not claude:
                raise ValidationError(f'roles.{name}: tier {tier_name!r} has no claude model')
            resolved['model'] = claude

        if 'route' in role:
            route = role['route']
            route_tier = _tier_or_raise(tiers, route['tier'], f'roles.{name}.route')
            claude = route_tier.get('claude')
            if not claude:
                raise ValidationError(
                    f'roles.{name}.route: tier {route["tier"]!r} has no claude model'
                )
            resolved['route'] = {
                'tier': route['tier'],
                'model': claude,
                'when': copy.deepcopy(route['when']),
            }

        if 'subagents' in role:
            subagents = {}
            for sub_name, sub in role['subagents'].items():
                sub_tier = _tier_or_raise(tiers, sub['tier'], f'roles.{name}.subagents.{sub_name}')
                dsh = sub_tier.get('dsh')
                if not dsh:
                    raise ValidationError(
                        f'roles.{name}.subagents.{sub_name}: tier {sub["tier"]!r} has no dsh model'
                    )
                subagents[sub_name] = {
                    'tier': sub['tier'],
                    'instructions': sub['instructions'],
                    'model': copy.deepcopy(dsh),
                }
            resolved['subagents'] = subagents

        roles[name] = resolved
    return roles


def _tier_routes(tiers: Mapping[str, Any]) -> list[dict[str, str]]:
    """Host allowedModels routes from low then mid, collapsing duplicates."""
    routes, seen = [], set()
    for name in ('low', 'mid'):
        dsh = (tiers.get(name) or {}).get('dsh')
        if not dsh:
            continue
        key = f"{dsh['provider']}\0{dsh['model']}"
        if key in seen:
            continue
        seen.add(key)
        routes.append({'provider': dsh['provider'], 'model': dsh['model']})
    return routes


def _surfaces(roles: Mapping[str, Any], tiers: Mapping[str, Any]) -> tuple:
    executor = None
    implementer = roles.get('implementer')
    if implementer and implementer.get('model'):
        executor = copy.deepcopy(implementer['model'])

    reviewer: dict[str, Any] = {}
    review_role = roles.get('reviewer')
    if review_role:
        if 'model' in review_role:
            reviewer['model'] = review_role['model']
        route = review_role.get('route')
        if route:
            reviewer['small_model'] = route['model']
            reviewer['small_diff_lines'] = route['when']['diff_lines_below']

    uat = None
    uat_role = roles.get('uat-tester')
    if uat_role and uat_role.get('model'):
        uat = copy.deepcopy(uat_role['model'])

    return executor, reviewer, uat, _tier_routes(tiers)


def _env(environ: Mapping[str, str], key: str) -> str:
    return (environ.get(key) or '').strip()


def _apply_dsh_env(
    environ: Mapping[str, str],
    provider_key: str,
    model_key: str,
    effort_key: str,
    label: str,
) -> dict[str, str] | None:
    provider = _env(environ, provider_key)
    model = _env(environ, model_key)
    effort = _env(environ, effort_key)
    if not (provider or model or effort):
        return None
    if not (provider and model):
        raise ValidationError(f'set both {provider_key} and {model_key} ({label})')
    out = {'provider': provider, 'model': model}
    if effort:
        out['reasoningEffort'] = effort
    return out


def _rewrite_subagents(roles: Mapping[str, Any], tier_name: str, model: dict[str, str]) -> None:
    for role in roles.values():
        for sub in (role.get('subagents') or {}).values():
            if sub.get('tier') == tier_name:
                sub['model'] = copy.deepcopy(model)


def _apply_env(resolved: ResolvedConfig, environ: Mapping[str, str]) -> None:
    executor_override = _apply_dsh_env(
        environ,
        'SUPERAGENT_EXECUTOR_PROVIDER',
        'SUPERAGENT_EXECUTOR_MODEL',
        'SUPERAGENT_EXECUTOR_REASONING_EFFORT',
        'executor',
    )
    if executor_override is not None:
        resolved.executor = executor_override
        if 'implementer' in resolved.roles:
            resolved.roles['implementer']['model'] = copy.deepcopy(executor_override)

    low = _apply_dsh_env(
        environ,
        'SUPERAGENT_TIER_LOW_PROVIDER',
        'SUPERAGENT_TIER_LOW_MODEL',
        'SUPERAGENT_TIER_LOW_REASONING_EFFORT',
        'tier low',
    )
    if low is not None:
        resolved.tiers.setdefault('low', {})['dsh'] = low
        _rewrite_subagents(resolved.roles, 'low', low)

    mid = _apply_dsh_env(
        environ,
        'SUPERAGENT_TIER_MID_PROVIDER',
        'SUPERAGENT_TIER_MID_MODEL',
        'SUPERAGENT_TIER_MID_REASONING_EFFORT',
        'tier mid',
    )
    if mid is not None:
        resolved.tiers.setdefault('mid', {})['dsh'] = mid
        _rewrite_subagents(resolved.roles, 'mid', mid)
        for name, role in resolved.roles.items():
            if role.get('tier') != 'mid' or role.get('runtime') not in ('dsh-worker', 'dsh-headless'):
                continue
            if name == 'implementer' and executor_override is not None:
                continue
            role['model'] = copy.deepcopy(mid)
        uat_env_set = bool(
            _env(environ, 'SUPERAGENT_UAT_PROVIDER')
            or _env(environ, 'SUPERAGENT_UAT_MODEL')
            or _env(environ, 'SUPERAGENT_UAT_REASONING_EFFORT')
        )
        if not uat_env_set and resolved.roles.get('uat-tester', {}).get('tier') == 'mid':
            resolved.roles['uat-tester']['model'] = copy.deepcopy(mid)
            resolved.uat = copy.deepcopy(mid)

    reviewer_model = _env(environ, 'SUPERAGENT_REVIEWER_MODEL')
    if reviewer_model:
        resolved.reviewer['model'] = reviewer_model
        if 'reviewer' in resolved.roles:
            resolved.roles['reviewer']['model'] = reviewer_model

    effort = _env(environ, 'SUPERAGENT_REVIEWER_EFFORT')
    if effort:
        if effort not in {'low', 'medium', 'high', 'xhigh', 'max'}:
            raise ValidationError(
                'SUPERAGENT_REVIEWER_EFFORT must be low, medium, high, xhigh or max'
            )
        resolved.reviewer['effort'] = effort

    small = _env(environ, 'SUPERAGENT_REVIEWER_SMALL_MODEL')
    if small:
        resolved.reviewer['small_model'] = small
        if 'reviewer' in resolved.roles and 'route' in resolved.roles['reviewer']:
            resolved.roles['reviewer']['route']['model'] = small

    threshold = _env(environ, 'SUPERAGENT_REVIEWER_SMALL_DIFF_LINES')
    if threshold:
        if not threshold.isdigit() or int(threshold) < 1:
            raise ValidationError('SUPERAGENT_REVIEWER_SMALL_DIFF_LINES must be a positive integer')
        value = int(threshold)
        resolved.reviewer['small_diff_lines'] = value
        if 'reviewer' in resolved.roles and 'route' in resolved.roles['reviewer']:
            resolved.roles['reviewer']['route']['when']['diff_lines_below'] = value

    uat = _apply_dsh_env(
        environ,
        'SUPERAGENT_UAT_PROVIDER',
        'SUPERAGENT_UAT_MODEL',
        'SUPERAGENT_UAT_REASONING_EFFORT',
        'uat',
    )
    if uat is not None:
        resolved.uat = uat
        if 'uat-tester' in resolved.roles:
            resolved.roles['uat-tester']['model'] = copy.deepcopy(uat)

    resolved.tier_routes = _tier_routes(resolved.tiers)


def load(path: str, environ: Mapping[str, str] | None = None) -> ResolvedConfig:
    """Load `path`, validate, resolve tiers, apply `environ` overrides (env wins)."""
    path = _require_absolute_deployment_path(path)
    document = _validate_document(_load_yaml(path))
    tiers = _resolve_tiers(document['tiers'])
    for role_name, role in document['roles'].items():
        _tier_or_raise(tiers, role['tier'], f'roles.{role_name}')
        if 'route' in role:
            _tier_or_raise(tiers, role['route']['tier'], f'roles.{role_name}.route')
        for sub_name, sub in (role.get('subagents') or {}).items():
            _tier_or_raise(tiers, sub['tier'], f'roles.{role_name}.subagents.{sub_name}')

    roles = _resolve_roles(document['roles'], tiers)
    executor, reviewer, uat, routes = _surfaces(roles, tiers)
    resolved = ResolvedConfig(
        version=document['version'],
        tiers=tiers,
        roles=roles,
        executor=executor,
        reviewer=reviewer,
        uat=uat,
        tier_routes=routes,
    )
    _apply_env(resolved, environ if environ is not None else os.environ)
    return resolved
