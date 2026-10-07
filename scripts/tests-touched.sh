#!/usr/bin/env bash
# Fails when a change touches source code without touching any test file.
# Opt-out: a non-empty .harness-no-tests (with the justification) changed in this same diff.
set -euo pipefail

cd "$(git rev-parse --show-toplevel)"

readonly EMPTY_TREE=4b825dc642cb6eb9a060e54bf8d69288fbee4904
readonly OPT_OUT_FILE=.harness-no-tests

resolve_base() {
  if ! git rev-parse --verify --quiet HEAD >/dev/null; then
    echo "$EMPTY_TREE"
    return
  fi
  if [[ -n "${BASE_REF:-}" ]]; then
    if ! git rev-parse --verify --quiet "${BASE_REF}^{commit}" >/dev/null; then
      echo "tests-touched: BASE_REF '${BASE_REF}' not found (fetch it first)" >&2
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

changed_files() {
  {
    git -c core.quotepath=off diff --name-only --no-ext-diff --diff-filter=ACMR "$BASE" --
    git -c core.quotepath=off ls-files --others --exclude-standard
  } | sort -u
}

is_test() {
  case "$1" in
    *.test.* | *.spec.* | test_*.py | */test_*.py | *_test.py | *Tests.swift | *Test.swift) return 0 ;;
    tests/* | */tests/* | test/* | */test/* | __tests__/* | */__tests__/* | Tests/* | */Tests/*) return 0 ;;
    *Tests/*) return 0 ;;
  esac
  return 1
}

is_source() {
  case "$1" in
    *.d.ts | *.config.* | Package.swift | */Package.swift | conftest.py | */conftest.py) return 1 ;;
    *.py | *.swift) return 0 ;;
  esac
  case "$1" in
    *.ts | *.tsx | *.js | *.jsx | *.mjs | *.cjs | *.mts | *.cts) ;;
    *) return 1 ;;
  esac
  case "$1" in
    src/* | */src/* | app/* | */app/* | lib/* | */lib/* | Sources/* | */Sources/*) return 0 ;;
  esac
  return 1
}

main() {
  BASE=$(resolve_base)

  local file sources=() has_tests=0 opted_out=0
  while IFS= read -r file; do
    [[ -z "$file" ]] && continue
    if [[ "$file" == "$OPT_OUT_FILE" ]] && grep -q '[^[:space:]]' "$file" 2>/dev/null; then
      opted_out=1
    elif is_test "$file"; then
      has_tests=1
    elif is_source "$file"; then
      sources+=("$file")
    fi
  done < <(changed_files)

  if [[ ${#sources[@]} -eq 0 || $has_tests -eq 1 ]]; then
    echo "tests-touched: ok"
    return
  fi
  if [[ $opted_out -eq 1 ]]; then
    echo "tests-touched: skipped by $OPT_OUT_FILE: $(head -n 1 "$OPT_OUT_FILE")"
    return
  fi

  echo "tests-touched: source files changed without any test change:" >&2
  printf '  %s\n' "${sources[@]}" >&2
  echo "Add/update tests, or (with user approval) write the justification to $OPT_OUT_FILE in this change." >&2
  exit 1
}

main "$@"
