#!/usr/bin/env bash
# Blocks lint/type suppressions, disabled tests and unauthorized schema changes.
# Only lines added since the merge-base with BASE_REF are scanned (committed, staged,
# unstaged and untracked), so legacy code never trips it.
set -euo pipefail

cd "$(git rev-parse --show-toplevel)"

readonly EMPTY_TREE=4b825dc642cb6eb9a060e54bf8d69288fbee4904
readonly ALLOW_SCHEMA_FILE=.harness/allow-schema
readonly SCHEMA_PATH_RE='(^|/)migrations/|(^|/)prisma/schema\.prisma$|\.sql$'

resolve_base() {
  if ! git rev-parse --verify --quiet HEAD >/dev/null; then
    echo "$EMPTY_TREE"
    return
  fi
  if [[ -n "${BASE_REF:-}" ]]; then
    if ! git rev-parse --verify --quiet "${BASE_REF}^{commit}" >/dev/null; then
      echo "guardrails: BASE_REF '${BASE_REF}' not found (fetch it first)" >&2
      return 1
    fi
    git merge-base "$BASE_REF" HEAD
    return
  fi
  local ref
  for ref in origin/HEAD origin/main origin/master main master; do
    if git rev-parse --verify --quiet "${ref}^{commit}" >/dev/null; then
      git merge-base "$ref" HEAD
      return
    fi
  done
  git rev-parse HEAD
}

# Emits "path<TAB>line<TAB>content" for every added line.
added_lines() {
  git -c core.quotepath=off diff --unified=0 --no-color --no-ext-diff --diff-filter=ACMR "$BASE" -- |
    awk '
      /^\+\+\+ / { file = substr($0, 7); next }
      /^@@/ { match($0, /\+[0-9]+/); line = substr($0, RSTART + 1, RLENGTH - 1) + 0; next }
      /^\+/ { print file "\t" line "\t" substr($0, 2); line++ }
    '
  local f
  git -c core.quotepath=off ls-files --others --exclude-standard | while IFS= read -r f; do
    if [[ -f "$f" ]] && grep -Iq . "$f" 2>/dev/null; then
      awk -v f="$f" '{ print f "\t" NR "\t" $0 }' "$f"
    fi
  done
}

schema_files() {
  {
    git -c core.quotepath=off diff --name-only --no-ext-diff "$BASE" --
    git -c core.quotepath=off ls-files --others --exclude-standard
  } | sort -u | grep -E "$SCHEMA_PATH_RE" || true
}

# Authorizations only count when added in this change, so old entries never
# silently authorize future schema changes.
schema_authorizations() {
  awk -F'\t' -v allow="$ALLOW_SCHEMA_FILE" '
    $1 == allow {
      entry = $3
      sub(/^[ \t]+/, "", entry)
      if (entry == "" || substr(entry, 1, 1) == "#") next
      split_at = index(entry, "::")
      if (split_at == 0) next
      glob = substr(entry, 1, split_at - 1); origin = substr(entry, split_at + 2)
      gsub(/^[ \t]+|[ \t]+$/, "", glob); gsub(/^[ \t]+|[ \t]+$/, "", origin)
      if (glob != "" && origin != "") print glob
    }
  ' "$ADDED"
}

is_schema_authorized() {
  local file=$1 glob
  while IFS= read -r glob; do
    # shellcheck disable=SC2053 # glob is intentionally unquoted to pattern-match
    [[ "$file" == $glob ]] && return 0
  done <"$AUTHORIZED"
  return 1
}

scan_lines() {
  awk -F'\t' -v schema_re="$SCHEMA_PATH_RE" '
    function report(rule) {
      printf "%s:%s: %s: %s\n", $1, $2, rule, content
      found = 1
    }
    {
      file = $1
      content = $0
      sub(/^[^\t]*\t[^\t]*\t/, "", content)
      if (content ~ /harness-allow:[ \t]*[^ \t]/) next

      if (file ~ /\.(ts|tsx|js|jsx|mjs|cjs|mts|cts|py|swift)$/) {
        if (content ~ /eslint-disable/) report("lint suppression (eslint-disable)")
        if (content ~ /@ts-(ignore|expect-error|nocheck)/) report("type suppression (@ts-*)")
        if (content ~ /#[ \t]*type:[ \t]*ignore/) report("type suppression (# type: ignore)")
        if (content ~ /#[ \t]*pyright:[ \t]*ignore/) report("type suppression (# pyright: ignore)")
        if (content ~ /#[ \t]*noqa/) report("lint suppression (# noqa)")
        if (content ~ /swiftlint:disable/) report("lint suppression (swiftlint:disable)")
        if (content ~ /\.(skip|only)\(/) report("disabled/focused test (.skip/.only)")
        if (content ~ /(^|[^A-Za-z0-9_.])x(it|describe|test)\(/) report("disabled test (xit/xdescribe/xtest)")
        if (content ~ /@(pytest\.mark\.skip|unittest\.skip)/) report("disabled test (skip decorator)")
        if (content ~ /XCTSkip|\.disabled\(/) report("disabled test (XCTSkip/.disabled)")
      }

      if (file ~ schema_re) {
        lower = tolower(content)
        if (lower ~ /drop[ \t]+(table|column)|truncate|delete[ \t]+from|alter[ \t].*[ \t]drop([ \t]|;|$)/)
          report("destructive schema statement (needs explicit user approval)")
      }
    }
    END { exit found ? 1 : 0 }
  ' "$ADDED"
}

main() {
  BASE=$(resolve_base)
  ADDED=$(mktemp)
  AUTHORIZED=$(mktemp)
  trap 'rm -f "$ADDED" "$AUTHORIZED"' EXIT

  added_lines >"$ADDED"
  schema_authorizations >"$AUTHORIZED"

  local failed=0 file
  if ! scan_lines; then
    failed=1
  fi

  while IFS= read -r file; do
    [[ -z "$file" ]] && continue
    if ! is_schema_authorized "$file"; then
      echo "$file: schema/migration change without authorization in $ALLOW_SCHEMA_FILE"
      failed=1
    fi
  done < <(schema_files)

  if [[ $failed -eq 1 ]]; then
    cat >&2 <<EOF

guardrails: FAILED (base: ${BASE:0:12})
- Suppressions and disabled tests need user approval and "harness-allow: <reason>" on the same line.
- Schema changes need an entry added in $ALLOW_SCHEMA_FILE: "<path or glob> :: issue #<n> (agent-ready)"
  or ":: aprovado pelo usuário em <YYYY-MM-DD>".
- Destructive statements always need explicit user approval plus "harness-allow: <reason>" on the line.
EOF
    exit 1
  fi
  echo "guardrails: ok"
}

main "$@"
