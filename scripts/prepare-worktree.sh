#!/bin/sh
# Set this absolute path as Superagent > Execution > Prepare command.
# Superagent injects the DSH_SUPERAGENT_* variables for each Ticket.
set -eu
: "${GITHUB_REPOSITORY:?}"
: "${DSH_SUPERAGENT_GITHUB_REPOSITORY:?}"
: "${DSH_SUPERAGENT_TICKET:?}"
: "${DSH_SUPERAGENT_NAME:?}"
: "${DSH_SUPERAGENT_DIR:?}"

if [ "$GITHUB_REPOSITORY" != "$DSH_SUPERAGENT_GITHUB_REPOSITORY" ]; then
  echo 'Ticket repository does not match configured GITHUB_REPOSITORY' >&2
  exit 1
fi

repo_name=${GITHUB_REPOSITORY#*/}
checkout="$HOME/src/$repo_name"

git -C "$checkout" fetch --no-tags \
  "https://github.com/$DSH_SUPERAGENT_GITHUB_REPOSITORY.git" \
  "${DSH_SUPERAGENT_GITHUB_PR_HEAD:-HEAD}:refs/dsh-superagent/$DSH_SUPERAGENT_TICKET"
git -C "$checkout" worktree add \
  -b "agent/$DSH_SUPERAGENT_NAME" \
  "$DSH_SUPERAGENT_DIR" \
  "refs/dsh-superagent/$DSH_SUPERAGENT_TICKET"
