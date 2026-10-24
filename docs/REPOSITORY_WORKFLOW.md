# Repository workflow

```
Civon/twitch-stream-recorder-dev (GitHub, private)  ──CI mirror──▶  gitlab.com/civon/twitch-stream-recorder-dev (private backup)
        │
        │ scripts/promote.sh (cherry-pick, run by a person)
        ▼
Civon/twitch-stream-recorder (GitHub, public) ◀── PR review ── promote/<topic>
```

- **dev** (private): day-to-day work, experiments, unfinished features.
- **public** (`origin`): only reviewed, finished features land here, through PRs.
- **GitLab** (private): a read-only backup of dev. Nobody works there.

## Least-privilege rules

| Credential | Where it lives | Scope | Can't do |
|---|---|---|---|
| `GITHUB_TOKEN` in the mirror job | dev repo Actions | `contents: read` on dev only | write to any GitHub repo |
| GitLab deploy key | dev repo secret `GITLAB_MIRROR_SSH_KEY` | git push to one GitLab project | GitLab API, other projects, your account |
| `GITHUB_TOKEN` in the GHCR job | Actions | `contents: read`, `packages: write` | push code, change settings |
| Your own login | your machine | push to public | — not stored in either repo's CI |

The dev repo holds **no credential for the public repo**. Promotion is a person
pushing a branch and opening a PR, so nothing in dev's CI can publish code.

## One-time setup (CLI)

Needs `gh` (logged in as Civon) and `glab` (logged in to gitlab.com). Run from a
clone of this repo. The order matters: secrets go in before the first push, so
the first mirror run already has them.

```bash
DEV=Civon/twitch-stream-recorder-dev
PUB=Civon/twitch-stream-recorder
GL=civon/twitch-stream-recorder-dev          # your GitLab namespace/project
GL_ID=${GL//\//%2F}

# 1. Private GitHub repo; Actions tokens read-only by default
gh repo create "$DEV" --private --disable-wiki \
  --description "Private dev repo for twitch-stream-recorder"
gh api -X PUT "repos/$DEV/actions/permissions/workflow" \
  -f default_workflow_permissions=read -F can_approve_pull_request_reviews=false

# 2. Private GitLab project (empty)
glab repo create "$GL" --private

# 3. Mirror-only SSH key, added as a write deploy key on that one project
ssh-keygen -t ed25519 -N '' -C 'github-actions mirror' -f gitlab_mirror
KEY_ID=$(glab api -X POST "projects/$GL_ID/deploy_keys" \
  -f title='github-actions mirror' -f key="$(cat gitlab_mirror.pub)" -f can_push=true | jq .id)

# 4. Protect feat/oauth on GitLab: only the deploy key may push (force allowed, to follow history)
glab api -X DELETE "projects/$GL_ID/protected_branches/feat%2Foauth" 2>/dev/null || true
cat > protect.json <<JSON
{"name":"feat/oauth","push_access_level":0,"merge_access_level":0,"allow_force_push":true,
 "allowed_to_push":[{"deploy_key_id":$KEY_ID}]}
JSON
glab api -X POST "projects/$GL_ID/protected_branches" \
  -H 'Content-Type: application/json' --input protect.json

# 5. Pin gitlab.com host key. Compare the fingerprint with
#    https://docs.gitlab.com/user/gitlab_com/#ssh-host-keys-fingerprints
ssh-keyscan -t ed25519 gitlab.com > gitlab_known_hosts
ssh-keygen -lf gitlab_known_hosts

# 6. Mirror secrets on the dev repo, then destroy the local private key
gh secret set GITLAB_MIRROR_SSH_KEY -R "$DEV" < gitlab_mirror
gh secret set GITLAB_KNOWN_HOSTS   -R "$DEV" < gitlab_known_hosts
gh variable set GITLAB_MIRROR_REPO -R "$DEV" --body "git@gitlab.com:$GL.git"
shred -u gitlab_mirror; rm -f gitlab_mirror.pub gitlab_known_hosts protect.json

# 7. Push history: origin becomes dev, public is the public repo
git remote rename origin public
git remote add origin "git@github.com:$DEV.git"
git push origin --all && git push origin --tags
gh repo edit "$DEV" --default-branch feat/oauth

# 8. Check the mirror ran
gh run list -R "$DEV" -w mirror-gitlab.yml -L 3
glab api "projects/$GL_ID/repository/branches" | jq -r '.[].name'

# 9. Public repo: changes only via PR, no force push or deletion
gh api -X PUT "repos/$PUB/branches/feat%2Foauth/protection" --input - <<'JSON'
{"required_status_checks":null,"enforce_admins":false,
 "required_pull_request_reviews":{"required_approving_review_count":0},
 "restrictions":null,"allow_force_pushes":false,"allow_deletions":false}
JSON
```

The mirror job is skipped everywhere except the dev repo, so the workflow file is
harmless when it reaches the public repo.

## Promoting a feature

```bash
git log --oneline public/feat/oauth..origin/feat/oauth   # what dev has that public doesn't
scripts/promote.sh oauth-refresh a1b2c3d e4f5a6b..9c8d7e6
```

The script branches `promote/<topic>` from `public/feat/oauth`, cherry-picks the
commits, pushes the branch to `public` and prints the PR link. Before merging,
check the diff for anything that shouldn't be public: notes, test streams,
`.env` values, hostnames.

Bring public-only changes (e.g. outside contributions) back into dev:

```bash
git fetch public && git merge public/feat/oauth
```

## Rotating the mirror key

Make a new key, add it as a deploy key (step 3), allow it on the protected
branch (step 4), replace the secret, then remove the old key:

```bash
gh secret set GITLAB_MIRROR_SSH_KEY -R "$DEV" < gitlab_mirror && shred -u gitlab_mirror
gh workflow run mirror-gitlab.yml -R "$DEV"
glab api -X DELETE "projects/$GL_ID/deploy_keys/$OLD_KEY_ID"
```
Do this at least yearly, or right away if dev's secrets may have leaked.
