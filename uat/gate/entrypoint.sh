#!/bin/sh
# Prepare the gate's volumes and the runner's TLS client certificate as root,
# then run the gate as the unprivileged `gate` user. The containers it starts on
# the runner run as that user too.
set -eu
chown gate:gate /var/lib/uat-gate /var/lib/uat-work
mkdir -p /run/uat-gate
chown gate:gate /run/uat-gate
chmod 0700 /run/uat-gate

if [ -n "${DOCKER_CERT_PATH:-}" ]; then
  # uat-runner (docker:dind) writes its CA and a client certificate on first start.
  tries=0
  until [ -s /certs/client/key.pem ] || [ "$tries" -ge 60 ]; do
    tries=$((tries + 1))
    sleep 2
  done
  if [ -s /certs/client/key.pem ]; then
    mkdir -p "$DOCKER_CERT_PATH"
    for file in ca.pem cert.pem key.pem; do
      cp "/certs/client/$file" "$DOCKER_CERT_PATH/$file"
    done
    chown -R gate:gate "$DOCKER_CERT_PATH"
    chmod 0700 "$DOCKER_CERT_PATH"
    chmod 0600 "$DOCKER_CERT_PATH"/*.pem
  else
    echo 'uat-gate: no runner client certificate in /certs/client; UAT runs will be BLOCKED' >&2
  fi
fi

# Apply UAT model from superagent.yaml when Coolify left SUPERAGENT_UAT_* empty
# (loader still lets a set env var win). Export into this process before the gate.
CONFIG_PATH="${SUPERAGENT_CONFIG_PATH:-/usr/local/share/superagent/superagent.yaml}"
if [ -f /usr/local/share/superagent/superagent_config.py ] && [ -f "$CONFIG_PATH" ]; then
  eval "$(python3 - "$CONFIG_PATH" <<'PY'
import importlib.util, os, shlex, sys

path = sys.argv[1]
spec = importlib.util.spec_from_file_location(
    'superagent_config', '/usr/local/share/superagent/superagent_config.py')
module = importlib.util.module_from_spec(spec)
sys.modules['superagent_config'] = module
spec.loader.exec_module(module)
try:
    resolved = module.load(path)
except module.ValidationError as error:
    sys.exit(f'uat-gate: {error}')
for key, value in module.uat_environ(resolved).items():
    print(f'export {key}={shlex.quote(value)}')
PY
)"
fi

exec su-exec gate "$@"
