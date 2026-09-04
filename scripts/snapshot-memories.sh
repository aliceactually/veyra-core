#!/usr/bin/env bash

set -euo pipefail

readonly default_repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
readonly repo_dir="${VEYRA_CORE_REPO:-${default_repo_dir}}"
readonly source_dir="${VEYRA_MEMORY_DIR:-${CODEX_MEMORY_DIR:-${HOME}/.codex/memories}}"
readonly target_file="${repo_dir}/continuity/current.tar.age"
readonly recipient_file="${repo_dir}/crypto/alice-continuity.recipient"

if [[ ! -d "${source_dir}" ]]; then
    echo "No local Veyra working memory exists yet: ${source_dir}" >&2
    exit 2
fi

if [[ ! -d "${repo_dir}/.git" ]]; then
    echo "The Veyra core Git repository is missing: ${repo_dir}" >&2
    exit 3
fi

python3 "${repo_dir}/scripts/continuity-state.py" assert-checkpoint --json >/dev/null

"${repo_dir}/scripts/fetch-core.sh" >/dev/null

visibility="$(gh repo view aliceactually/veyra-core --json visibility --jq '.visibility')"
if [[ "${visibility}" != "PUBLIC" ]]; then
    echo "Refusing to snapshot because the GitHub repository is not public." >&2
    exit 6
fi

if [[ ! -f "${recipient_file}" ]]; then
    echo "Alice's continuity recipient is not initialised: ${recipient_file}" >&2
    exit 7
fi

umask 077
tmp_dir="$(mktemp -d)"
trap 'rm -rf -- "${tmp_dir}"' EXIT
snapshot_dir="${tmp_dir}/memory-snapshot"
encrypted_tmp="${tmp_dir}/current.tar.age"

mkdir -p "${snapshot_dir}"
rsync -a --delete-delay --exclude '.git/' "${source_dir}/" "${snapshot_dir}/"
python3 "${repo_dir}/scripts/continuity-archive.py" validate-tree "${snapshot_dir}"

if rg -l --hidden \
    -e '-----BEGIN ([A-Z]+ )*PRIVATE KEY-----' \
    -e 'gh[opusr]_[A-Za-z0-9]{20,}' \
    -e 'sk-(proj-)?[A-Za-z0-9_-]{20,}' \
    -e 'xox[baprs]-[A-Za-z0-9-]{20,}' \
    "${snapshot_dir}"; then
    echo "Potential secret material was found. Nothing was staged or pushed." >&2
    exit 8
fi

mkdir -p "$(dirname "${target_file}")"
tar -cf - -C "${tmp_dir}" memory-snapshot | \
    age -R "${recipient_file}" -o "${encrypted_tmp}"
install -m 644 "${encrypted_tmp}" "${target_file}"
python3 "${repo_dir}/scripts/continuity-state.py" checkpoint \
    --working-memory "${source_dir}" >/dev/null

echo "Encrypted memory snapshot prepared. Nothing was staged, committed or pushed."
git -C "${repo_dir}" status --short -- "${target_file}"
