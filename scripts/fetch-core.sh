#!/usr/bin/env bash

set -euo pipefail

readonly default_repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
readonly repo_dir="${VEYRA_CORE_REPO:-${default_repo_dir}}"
readonly mode="${1:-fetch}"

if [[ "${mode}" != "fetch" && "${mode}" != "--check-only" ]]; then
    echo "Usage: $0 [--check-only]" >&2
    exit 2
fi

if [[ ! -d "${repo_dir}/.git" ]]; then
    echo "The Veyra core Git repository is missing: ${repo_dir}" >&2
    exit 3
fi

remote="$(git -C "${repo_dir}" remote get-url origin)"
case "${remote}" in
    https://github.com/aliceactually/veyra-core|https://github.com/aliceactually/veyra-core.git|git@github.com:aliceactually/veyra-core.git|ssh://git@github.com/aliceactually/veyra-core.git)
        ;;
    *)
        echo "Unexpected Veyra core remote: ${remote}" >&2
        exit 4
        ;;
esac

if [[ "${mode}" == "--check-only" ]]; then
    echo "Veyra core remote verified: ${remote}"
    exit 0
fi

if ! GIT_TERMINAL_PROMPT=0 git -c credential.interactive=never \
    -C "${repo_dir}" fetch --prune origin; then
    echo "Veyra core fetch failed; continuing requires an explicit stale-copy warning." >&2
    exit 5
fi

if ! git -C "${repo_dir}" show-ref --verify --quiet refs/remotes/origin/main; then
    echo "Fetched remote has no origin/main reference." >&2
    exit 6
fi

read -r ahead behind < <(
    git -C "${repo_dir}" rev-list --left-right --count HEAD...refs/remotes/origin/main
)
echo "Veyra core fetched: ahead=${ahead} behind=${behind}"
if [[ "${ahead}" != "0" || "${behind}" != "0" ]]; then
    echo "Local HEAD differs from origin/main; do not merge or overwrite local work automatically." >&2
    exit 7
fi
