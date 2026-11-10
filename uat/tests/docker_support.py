"""Helpers for the tests that run real containers; they skip without a Docker daemon."""
import os
import shutil
import subprocess
import unittest

UAT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TESTER_IMAGE = 'superagent-uat-tester:test'
SANDBOX_IMAGE = 'superagent-uat-sandbox:test'


def docker(*args, check=True, **kwargs):
    return subprocess.run(['docker', *args], capture_output=True, text=True, check=check, **kwargs)


def require_docker():
    if not shutil.which('docker') or subprocess.run(['docker', 'info'], capture_output=True).returncode:
        raise unittest.SkipTest('needs a Docker daemon')
    if os.environ.get('SUPERAGENT_UAT_SKIP_DOCKER_TESTS'):
        raise unittest.SkipTest('SUPERAGENT_UAT_SKIP_DOCKER_TESTS is set')


def build(tag, dockerfile):
    """Build an image from uat/ (cached after the first build)."""
    docker('build', '-q', '-t', tag, '-f', os.path.join(UAT, dockerfile), UAT, timeout=1800)


def leftovers(prefix):
    """Names of containers, networks and volumes still carrying the prefix."""
    found = []
    for kind in ('container', 'network', 'volume'):
        listing = docker(kind, 'ls', *(['-a'] if kind == 'container' else []), '--format',
                         '{{.Names}}' if kind == 'container' else '{{.Name}}').stdout.split()
        found += [f'{kind} {name}' for name in listing if name.startswith(prefix)]
    return found
