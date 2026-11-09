#!/bin/sh
set -eu
umask 077

: "${DEEPSEEK_API_KEY:?Set DEEPSEEK_API_KEY in Coolify Environment Variables}"
: "${GH_TOKEN:?Set a classic GitHub personal access token in Coolify Environment Variables}"
: "${GITHUB_REPOSITORY:?Set GITHUB_REPOSITORY to OWNER/REPO}"
: "${SERVICE_URL_DASHBOARD:?Assign a domain to the dashboard service in Coolify}"
: "${SERVICE_FQDN_DASHBOARD:?Assign a domain to the dashboard service in Coolify}"
# The Delivery requirements merge a PR only after a Claude Code review passes.
[ -n "${ANTHROPIC_API_KEY:-}${CLAUDE_CODE_OAUTH_TOKEN:-}" ] || {
  echo 'Set ANTHROPIC_API_KEY or CLAUDE_CODE_OAUTH_TOKEN for the Claude Code PR reviewer' >&2
  exit 1
}
case "${SUPERAGENT_REVIEWER_EFFORT:-}" in
  ''|low|medium|high|xhigh|max) ;;
  *) echo 'SUPERAGENT_REVIEWER_EFFORT must be low, medium, high, xhigh or max' >&2; exit 1 ;;
esac
case "${SUPERAGENT_UAT_APP_ID:-}" in
  ''|*[!0-9]*) [ -z "${SUPERAGENT_UAT_APP_ID:-}" ] || {
    echo 'SUPERAGENT_UAT_APP_ID must be the numeric id of the UAT GitHub App' >&2; exit 1; } ;;
  *) uat_token=${SUPERAGENT_UAT_GATE_TOKEN:-}
     [ "${#uat_token}" -ge 32 ] || {
       echo 'Set SUPERAGENT_UAT_GATE_TOKEN (32+ characters, the same in uat-gate) to use user acceptance tests' >&2
       exit 1; } ;;
esac

# Validate OWNER/REPO before deriving a local checkout path.
python3 - "$GITHUB_REPOSITORY" <<'PY'
import re
import sys
if not re.fullmatch(r'[A-Za-z0-9-]+/[A-Za-z0-9_.-]+', sys.argv[1]):
    sys.exit('GITHUB_REPOSITORY must have the form OWNER/REPO')
PY

# Avoid writing the PAT into persistent DSH files or GitHub CLI config.
# NOTE: Docker/Coolify still has this secret as a configured environment value.
# The tmpfs is owned by UID/GID 10001 through compose.yaml.
install -m 0600 /dev/null /run/superagent-secrets/github-token
printf '%s' "$GH_TOKEN" > /run/superagent-secrets/github-token
unset GH_TOKEN GITHUB_TOKEN

# claude-review reads the Claude Code credential, model and effort from here:
# DSH strips *_KEY/*_TOKEN variables from worker shells, and neither DSH nor
# DeepSeek needs them in their environment.
install -m 0600 /dev/null /run/superagent-secrets/claude-review.json
python3 > /run/superagent-secrets/claude-review.json <<'PY'
import json, os
credentials = {name: os.environ[name] for name in ('ANTHROPIC_API_KEY', 'CLAUDE_CODE_OAUTH_TOKEN') if os.environ.get(name)}
effort = os.environ.get('SUPERAGENT_REVIEWER_EFFORT')
print(json.dumps({'model': os.environ.get('SUPERAGENT_REVIEWER_MODEL', ''),
                  'env': {**credentials, **({'CLAUDE_CODE_EFFORT_LEVEL': effort} if effort else {})}}))
PY
unset ANTHROPIC_API_KEY CLAUDE_CODE_OAUTH_TOKEN

# uat-test reads the gate's URL, the request token and the id of the GitHub App
# whose superagent/uat check it trusts. The token only lets a requester ask for
# runs; the gate decides and publishes everything itself. No file: UAT is off.
rm -f /run/superagent-secrets/uat.json
if [ -n "${SUPERAGENT_UAT_APP_ID:-}" ]; then
  install -m 0600 /dev/null /run/superagent-secrets/uat.json
  python3 > /run/superagent-secrets/uat.json <<'PY'
import json, os
print(json.dumps({'app_id': int(os.environ['SUPERAGENT_UAT_APP_ID']),
                  'gate_url': os.environ.get('SUPERAGENT_UAT_GATE_URL') or 'http://uat-gate:8090',
                  'token': os.environ['SUPERAGENT_UAT_GATE_TOKEN'],
                  'check_name': os.environ.get('SUPERAGENT_UAT_CHECK_NAME') or 'superagent/uat'}))
PY
fi
unset SUPERAGENT_UAT_GATE_TOKEN

# Use exactly the agent identity supplied by the token; never interactive login.
/usr/local/bin/gh-agent api user --jq .login > /dev/null || {
  echo 'GitHub authentication failed; verify GH_TOKEN (classic PAT).' >&2
  exit 1
}

repo_name=${GITHUB_REPOSITORY#*/}
repo_checkout="$HOME/src/$repo_name"
if [ ! -e "$repo_checkout" ]; then
  echo "Cloning GitHub repository $GITHUB_REPOSITORY into $repo_checkout"
  git -c credential.helper= \
      -c 'credential.helper=!/usr/local/bin/gh-agent auth git-credential' \
      clone "https://github.com/$GITHUB_REPOSITORY.git" "$repo_checkout"
elif [ ! -d "$repo_checkout/.git" ]; then
  echo "Checkout path exists but is not a Git clone: $repo_checkout" >&2
  exit 1
fi

# DSH runs on localhost (3082); expose 3083 ONLY to the internal dashboard sidecar.
socat TCP-LISTEN:3083,bind=0.0.0.0,reuseaddr,fork TCP:127.0.0.1:3082 &
# Apply the environment's Superagent settings once DSH answers. The subshell
# orphans it to tini (compose `init: true`), which reaps it when it finishes.
( /usr/local/bin/superagent-configure & )
# Retry tickets waiting for dependencies once those are done, every minute.
( /usr/local/bin/superagent-dependencies & )
exec "$@"
