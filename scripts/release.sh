#!/usr/bin/env bash
# piPalette release helper.
#
# Usage:
#   scripts/release.sh                        # interactive, defaults to patch bump
#   scripts/release.sh --minor                # bump minor (X.(Y+1).0)
#   scripts/release.sh --major                # bump major ((X+1).0.0)
#   scripts/release.sh --version v0.2.0       # specific version
#
# What this script does:
#   1. Pre-flight checks (clean working tree, on master, in sync with origin).
#   2. Computes the next version from the latest v* tag.
#   3. Shows commits since the last tag, asks you to confirm.
#   4. Opens $EDITOR with a pre-filled annotation grouped by prefix.
#   5. Creates the annotated tag and pushes it.
#   6. GHA's release.yml workflow picks it up — builds the PDF, publishes
#      the GitHub Release, attaches the PDF. Nothing else local to do.
#
# Commit prefix convention (drives the release-note grouping):
#   new:      a feature users will notice          → ## New
#   fix:      a bug users were hitting             → ## Fixed
#   improve:  perf, UX polish, refactor with user-visible effect → ## Improved
#   docs:     manual / README / in-app copy        → ## Documentation
#   internal: CI, release tooling, dev scripts     → hidden from release notes
#   (anything else)                                → ## Other
#
# The prefix is parsed off when grouping, so a commit
# "fix: snapshot live FileList" becomes "- snapshot live FileList"
# under "## Fixed" in the annotation. Edit further in $EDITOR before tagging.

set -euo pipefail

cd "$(git rev-parse --show-toplevel)"

bold()  { printf '\033[1m%s\033[0m\n' "$*"; }
warn()  { printf '\033[33m%s\033[0m\n' "$*"; }
err()   { printf '\033[31m%s\033[0m\n' "$*" >&2; }
ok()    { printf '\033[32m%s\033[0m\n' "$*"; }

usage() {
  sed -n '/^# piPalette release helper/,/^$/p' "$0" | sed 's/^# \{0,1\}//'
  exit "${1:-0}"
}

# -- parse args ---------------------------------------------------------

BUMP="patch"
EXPLICIT_VERSION=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --patch)   BUMP="patch" ;;
    --minor)   BUMP="minor" ;;
    --major)   BUMP="major" ;;
    --version) EXPLICIT_VERSION="${2:?--version needs an argument}"; shift ;;
    -h|--help) usage 0 ;;
    *)         err "Unknown flag: $1"; usage 2 ;;
  esac
  shift
done

# -- pre-flight checks --------------------------------------------------

if [[ -n "$(git status --porcelain)" ]]; then
  err "Working tree isn't clean. Commit or stash your changes first."
  git status --short
  exit 1
fi

CURRENT_BRANCH="$(git rev-parse --abbrev-ref HEAD)"
if [[ "$CURRENT_BRANCH" != "master" ]]; then
  err "Not on master (currently on $CURRENT_BRANCH). Releases come from master."
  exit 1
fi

bold "Fetching latest from origin…"
git fetch --quiet --tags --prune origin master

LOCAL_HEAD="$(git rev-parse HEAD)"
REMOTE_HEAD="$(git rev-parse origin/master)"
if [[ "$LOCAL_HEAD" != "$REMOTE_HEAD" ]]; then
  err "Local master and origin/master have diverged."
  err "  local : $LOCAL_HEAD"
  err "  remote: $REMOTE_HEAD"
  err "Push or pull until they match, then re-run."
  exit 1
fi

# -- pick the next version ----------------------------------------------

LAST_TAG="$(git tag --sort=-v:refname | grep -E '^v[0-9]+\.[0-9]+\.[0-9]+$' | head -1 || true)"

if [[ -n "$EXPLICIT_VERSION" ]]; then
  if [[ ! "$EXPLICIT_VERSION" =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
    err "Bad version format: $EXPLICIT_VERSION (expected vX.Y.Z)"
    exit 1
  fi
  NEXT_TAG="$EXPLICIT_VERSION"
elif [[ -z "$LAST_TAG" ]]; then
  NEXT_TAG="v0.1.0"
  warn "No existing tags found; defaulting to $NEXT_TAG"
else
  IFS='.' read -r MAJOR MINOR PATCH <<< "${LAST_TAG#v}"
  case "$BUMP" in
    patch) PATCH=$((PATCH + 1)) ;;
    minor) MINOR=$((MINOR + 1)); PATCH=0 ;;
    major) MAJOR=$((MAJOR + 1)); MINOR=0; PATCH=0 ;;
  esac
  NEXT_TAG="v${MAJOR}.${MINOR}.${PATCH}"
fi

if git rev-parse --verify --quiet "$NEXT_TAG" >/dev/null; then
  err "Tag $NEXT_TAG already exists. Pick a different version."
  exit 1
fi

echo
bold "About to release $NEXT_TAG  (previous: ${LAST_TAG:-none})"
echo

if [[ -n "$LAST_TAG" ]]; then
  echo "Commits since $LAST_TAG:"
  git log "$LAST_TAG..HEAD" --pretty=format:'  %h %s' --no-merges
else
  echo "All commits (no previous tag):"
  git log --pretty=format:'  %h %s' --no-merges | head -50
fi
echo
echo

read -rp "Continue? [y/N] " yn
[[ "$yn" =~ ^[yY] ]] || { echo "Aborted."; exit 0; }

# -- prepare and edit the annotation ------------------------------------

TMPFILE="$(mktemp)"
trap 'rm -f "$TMPFILE"' EXIT

# Group commit subjects by their conventional prefix so the annotation
# starts as a readable, user-facing summary instead of a flat dev log.
group_commits_by_prefix() {
  local range="$1"
  local -a new=() fixed=() improved=() docs=() other=()

  while IFS= read -r subject; do
    [[ -z "$subject" ]] && continue
    case "$subject" in
      new:*)
        new+=("${subject#new:}") ;;
      fix:*)
        fixed+=("${subject#fix:}") ;;
      improve:*)
        improved+=("${subject#improve:}") ;;
      docs:*)
        docs+=("${subject#docs:}") ;;
      internal:*)
        # intentionally hidden — internal commits don't belong in
        # user-facing release notes (CI, release tooling, etc.)
        ;;
      *)
        other+=("$subject") ;;
    esac
  done < <(git log $range --pretty=format:'%s' --no-merges)

  emit_section() {
    local title="$1"; shift
    local -a items=("$@")
    if (( ${#items[@]} )); then
      printf '## %s\n' "$title"
      printf -- '- %s\n' "${items[@]/# /}"  # trim leading space if any
      echo
    fi
  }
  emit_section "New" "${new[@]}"
  emit_section "Fixed" "${fixed[@]}"
  emit_section "Improved" "${improved[@]}"
  emit_section "Documentation" "${docs[@]}"
  emit_section "Other" "${other[@]}"
}

{
  echo "piPalette $NEXT_TAG"
  echo
  if [[ -n "$LAST_TAG" ]]; then
    group_commits_by_prefix "$LAST_TAG..HEAD"
  else
    # No previous tag — group everything; cap at 50 commits for sanity.
    group_commits_by_prefix "-n 50"
  fi
} > "$TMPFILE"

EDITOR_CMD="${EDITOR:-nano}"
"$EDITOR_CMD" "$TMPFILE"

# Sanity-check: annotation must have some non-whitespace, non-comment content.
if ! grep -q '[^[:space:]#]' "$TMPFILE"; then
  err "Annotation is empty. Aborting — no tag created."
  exit 1
fi

# -- final confirmation, then tag + push --------------------------------

echo
bold "Final preview of $NEXT_TAG annotation:"
echo "─────────────────────────────────────────"
cat "$TMPFILE"
echo "─────────────────────────────────────────"
echo

read -rp "Push $NEXT_TAG to origin? [y/N] " yn
[[ "$yn" =~ ^[yY] ]] || { echo "Aborted (no tag created)."; exit 0; }

git tag -a "$NEXT_TAG" -F "$TMPFILE"
git push origin "$NEXT_TAG"

echo
ok "Tagged + pushed $NEXT_TAG."
echo
echo "GitHub Actions is now building the manual and creating the Release."
echo "  Workflow run: https://github.com/veroc/pipalette/actions"
echo "  Release page: https://github.com/veroc/pipalette/releases/tag/$NEXT_TAG"
echo "                (appears once the workflow completes, ~2-3 min)"
