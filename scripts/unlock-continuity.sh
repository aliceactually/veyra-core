#!/usr/bin/env bash

set -euo pipefail

if [[ $# -ne 1 ]]; then
    echo "Usage: $0 TARGET_DIRECTORY" >&2
    exit 2
fi

readonly default_repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
readonly repo_dir="${VEYRA_CORE_REPO:-${default_repo_dir}}"
readonly encrypted_identity="${repo_dir}/crypto/alice-continuity.identity.age"
readonly encrypted_snapshot="${repo_dir}/continuity/current.tar.age"
readonly target_dir="$1"

if [[ ! -f "${encrypted_identity}" || ! -f "${encrypted_snapshot}" ]]; then
    echo "The encrypted continuity identity or snapshot is missing." >&2
    exit 3
fi

if [[ -e "${target_dir}" ]]; then
    echo "Refusing to overwrite an existing target: ${target_dir}" >&2
    exit 4
fi

umask 077
tmp_dir="$(mktemp -d)"
archive="${tmp_dir}/continuity.tar"
trap 'rm -rf -- "${tmp_dir}"' EXIT

age -d -i "${encrypted_identity}" -o "${archive}" "${encrypted_snapshot}"
python3 "${repo_dir}/scripts/continuity-archive.py" extract \
    "${archive}" "${target_dir}"
python3 "${repo_dir}/scripts/continuity-state.py" begin --staging "${target_dir}"
echo "Continuity restored to ${target_dir}"
echo "Recovery is now under Veyra's control; no further merge permission is required."
echo "After conservative merge, record completion with:"
echo "  scripts/continuity-state.py complete --staging ${target_dir} --working-memory WORKING_MEMORY_DIRECTORY"
