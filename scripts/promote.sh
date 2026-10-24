#!/usr/bin/env bash
# Cherry-pick finished commits from the private dev repo onto a new branch of
# the public repo, then open a PR there. Run from a clone of the dev repo.
#
#   git remote add public git@github.com:Civon/twitch-stream-recorder.git   # once
#   scripts/promote.sh <topic> <commit|range>...
#   scripts/promote.sh oauth-refresh a1b2c3d e4f5a6b..9c8d7e6
#
# Uses your own git credentials; the dev repo and its CI hold no token for the public repo.
set -euo pipefail

PUBLIC_REMOTE=${PUBLIC_REMOTE:-public}
PUBLIC_BASE=${PUBLIC_BASE:-feat/oauth}

if [ $# -lt 2 ]; then
  sed -n '2,9p' "$0" | sed 's/^# \{0,1\}//'
  exit 1
fi

topic=$1; shift
branch="promote/$topic"

if [ -n "$(git status --porcelain)" ]; then
  echo "Working tree is not clean; commit or stash first." >&2
  exit 1
fi

start=$(git rev-parse --abbrev-ref HEAD)
git fetch "$PUBLIC_REMOTE" "$PUBLIC_BASE"
git switch -c "$branch" "$PUBLIC_REMOTE/$PUBLIC_BASE"

# No -x: the private commit SHAs mean nothing in the public history.
if ! git cherry-pick "$@"; then
  echo "Conflict. Resolve, then 'git cherry-pick --continue' and re-run the push below," >&2
  echo "or 'git cherry-pick --abort && git switch $start && git branch -D $branch'." >&2
  echo "  git push -u $PUBLIC_REMOTE $branch" >&2
  exit 1
fi

git push -u "$PUBLIC_REMOTE" "$branch"
git switch "$start"
echo "Open the PR: https://github.com/Civon/twitch-stream-recorder/compare/$PUBLIC_BASE...$branch?expand=1"
