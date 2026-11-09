"""Validate data against uat/*.schema.json with a JSON Schema subset.

The controller runs on stock GitHub runners with only the Python standard
library, so this implements exactly the keywords the two schemas use and
refuses any other keyword: a schema edit cannot silently stop validating.
tests/test_schema.py checks the results against the reference `jsonschema`
package when it is installed.
"""
import json
import os
import re

DIRECTORY = os.path.dirname(os.path.abspath(__file__))
ANNOTATIONS = {'$schema', '$id', '$defs', '$comment', 'title', 'description'}
TYPES = {
    'object': lambda value: isinstance(value, dict),
    'array': lambda value: isinstance(value, list),
    'string': lambda value: isinstance(value, str),
    'integer': lambda value: isinstance(value, int) and not isinstance(value, bool),
    'boolean': lambda value: isinstance(value, bool),
}


def load(name):
    """The schema uat/<name>.schema.json."""
    with open(os.path.join(DIRECTORY, f'{name}.schema.json'), encoding='utf-8') as file:
        return json.load(file)


def errors(instance, schema, root=None, path='$'):
    """Every violation of `schema` by `instance`, as '<json path>: <problem>' strings."""
    root = schema if root is None else root
    found = []
    for keyword in schema:
        if keyword not in ANNOTATIONS and keyword not in CHECKS:
            raise ValueError(f'unsupported schema keyword {keyword!r} at {path}')
    if '$ref' in schema:
        found += errors(instance, _resolve(root, schema['$ref']), root, path)
    if 'type' in schema and not TYPES[schema['type']](instance):
        return found + [f'{path}: must be of type {schema["type"]}']
    for keyword, check in CHECKS.items():
        if keyword in schema and keyword != '$ref':
            found += check(instance, schema[keyword], schema, root, path)
    return found


def _resolve(root, reference):
    if not reference.startswith('#/'):
        raise ValueError(f'only local references are supported, not {reference!r}')
    node = root
    for part in reference[2:].split('/'):
        node = node[part]
    return node


def _same(a, b):
    """JSON equality: 1 and True differ, unlike in Python."""
    return type(a) is type(b) and a == b if isinstance(a, bool) or isinstance(b, bool) else a == b


def _properties(instance, properties, schema, root, path):
    if not isinstance(instance, dict):
        return []
    return [error for name, sub in properties.items() if name in instance
            for error in errors(instance[name], sub, root, f'{path}.{name}')]


def _additional(instance, additional, schema, root, path):
    if not isinstance(instance, dict):
        return []
    extra = [name for name in instance if name not in schema.get('properties', {})]
    if additional is False:
        return [f'{path}: unexpected property {name!r}' for name in extra]
    return [error for name in extra for error in errors(instance[name], additional, root, f'{path}.{name}')]


def _items(instance, items, schema, root, path):
    if not isinstance(instance, list):
        return []
    return [error for index, item in enumerate(instance) for error in errors(item, items, root, f'{path}[{index}]')]


def _one_of(instance, options, schema, root, path):
    matching = sum(not errors(instance, option, root, path) for option in options)
    return [] if matching == 1 else [f'{path}: must match exactly one allowed form (matches {matching})']


def _bounded(kind, measure, message):
    def check(instance, bound, schema, root, path):
        if not kind(instance):
            return []
        return [f'{path}: {message} {bound}'] if measure(instance, bound) else []
    return check


def _is_number(value):
    return isinstance(value, int) and not isinstance(value, bool)


CHECKS = {
    '$ref': lambda *args: [],
    'type': lambda *args: [],
    'const': lambda instance, const, schema, root, path: [] if _same(instance, const) else [f'{path}: must be {const!r}'],
    'enum': lambda instance, options, schema, root, path:
        [] if any(_same(instance, option) for option in options) else [f'{path}: must be one of {options}'],
    'pattern': lambda instance, pattern, schema, root, path:
        [] if not isinstance(instance, str) or re.search(pattern, instance) else [f'{path}: does not match {pattern}'],
    'minLength': _bounded(lambda v: isinstance(v, str), lambda v, b: len(v) < b, 'length must be at least'),
    'maxLength': _bounded(lambda v: isinstance(v, str), lambda v, b: len(v) > b, 'length must be at most'),
    'minimum': _bounded(_is_number, lambda v, b: v < b, 'must be at least'),
    'maximum': _bounded(_is_number, lambda v, b: v > b, 'must be at most'),
    'minItems': _bounded(lambda v: isinstance(v, list), lambda v, b: len(v) < b, 'item count must be at least'),
    'maxItems': _bounded(lambda v: isinstance(v, list), lambda v, b: len(v) > b, 'item count must be at most'),
    'minProperties': _bounded(lambda v: isinstance(v, dict), lambda v, b: len(v) < b, 'property count must be at least'),
    'maxProperties': _bounded(lambda v: isinstance(v, dict), lambda v, b: len(v) > b, 'property count must be at most'),
    'uniqueItems': lambda instance, unique, schema, root, path:
        [f'{path}: items must be unique'] if unique and isinstance(instance, list)
        and len({json.dumps(item, sort_keys=True) for item in instance}) != len(instance) else [],
    'required': lambda instance, names, schema, root, path:
        [f'{path}: missing property {name!r}' for name in names if isinstance(instance, dict) and name not in instance],
    'properties': _properties,
    'additionalProperties': _additional,
    'propertyNames': lambda instance, sub, schema, root, path:
        [error for name in (instance if isinstance(instance, dict) else ())
         for error in errors(name, sub, root, f'{path} key {name!r}')],
    'items': _items,
    'oneOf': _one_of,
}
