#!/bin/sh
set -eu
umask 077

: "${DEEPSEEK_API_KEY:?Set DEEPSEEK_API_KEY in Coolify Environment Variables}"
: "${GH_TOKEN:?Set a classic GitHub personal access token in Coolify Environment Variables}"
: "${GITHUB_REPOSITORY:?Set GITHUB_REPOSITORY to OWNER/REPO}"

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

# DSH runs on localhost (3082); expose 3083 ONLY to the internal dashboard sidecar.
socat TCP-LISTEN:3083,bind=0.0.0.0,reuseaddr,fork TCP:127.0.0.1:3082 &
exec "$@"
