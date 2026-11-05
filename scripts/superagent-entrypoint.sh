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

# A profile seeded by an image without the Claude Code bundle (an older
# superagent_home volume) gets it once; fresh volumes have it from the image.
bundle=${DSH_CLAUDE_CODE_BUNDLE%@*}
if ! jq -e --arg bundle "$bundle" '.dependencies[$bundle]' \
    "$DSH_HOME/profiles/superagent/package.json" > /dev/null; then
  echo "Adding $DSH_CLAUDE_CODE_BUNDLE to the superagent profile"
  dsh plugin --profile superagent add "$DSH_CLAUDE_CODE_BUNDLE"
fi

# DSH runs on localhost (3082); expose 3083 ONLY to the internal dashboard sidecar.
socat TCP-LISTEN:3083,bind=0.0.0.0,reuseaddr,fork TCP:127.0.0.1:3082 &
# Apply the environment's Superagent settings once DSH answers. The subshell
# orphans it to tini (compose `init: true`), which reaps it when it finishes.
( /usr/local/bin/superagent-configure & )
exec "$@"
