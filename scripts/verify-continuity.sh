#!/usr/bin/env bash

set -euo pipefail

readonly default_repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
readonly repo_dir="${VEYRA_CORE_REPO:-${default_repo_dir}}"
readonly source_memory="${VEYRA_MEMORY_DIR:-${CODEX_MEMORY_DIR:-${HOME}/.codex/memories}}"
readonly encrypted_identity="${repo_dir}/crypto/alice-continuity.identity.age"
readonly expected_recipient="${repo_dir}/crypto/alice-continuity.recipient"
readonly current_snapshot="${repo_dir}/continuity/current.tar.age"
readonly previous_state="${repo_dir}/continuity/pre-encryption-state.tar.age"

for path in \
    "${encrypted_identity}" \
    "${expected_recipient}" \
    "${current_snapshot}" \
    "${previous_state}"; do
    if [[ ! -f "${path}" ]]; then
        echo "Required continuity file is missing: ${path}" >&2
        exit 2
    fi
done

umask 077
tmp_dir="$(mktemp -d)"
trap 'rm -rf -- "${tmp_dir}"' EXIT
alice_plain="${tmp_dir}/alice-identity.txt"
actual_recipient="${tmp_dir}/alice-recipient.txt"

echo "Unlock Alice's continuity identity to verify both encrypted archives."
age -d -o "${alice_plain}" "${encrypted_identity}"
age-keygen -y -o "${actual_recipient}" "${alice_plain}"
if ! cmp -s "${actual_recipient}" "${expected_recipient}"; then
    echo "Alice's recovered identity does not match the public recipient." >&2
    exit 3
fi

verify_archive() {
    local encrypted="$1"
    local archive="$2"
    local extracted="$3"

    age -d -i "${alice_plain}" -o "${archive}" "${encrypted}"
    python3 "${repo_dir}/scripts/continuity-archive.py" extract \
        "${archive}" "${extracted}"
}

verify_archive \
    "${current_snapshot}" \
    "${tmp_dir}/current.tar" \
    "${tmp_dir}/current"

current_diff="$(rsync -ani --delete \
    "${source_memory}/" \
    "${tmp_dir}/current/memory-snapshot/")"
if [[ -n "${current_diff}" ]]; then
    echo "Current encrypted memory snapshot differs from the live source:" >&2
    echo "${current_diff}" >&2
    exit 4
fi

verify_archive \
    "${previous_state}" \
    "${tmp_dir}/previous.tar" \
    "${tmp_dir}/previous"

for relative in checkpoints memory-snapshots/current; do
    if [[ -d "${repo_dir}/${relative}" ]]; then
        previous_diff="$(rsync -ani --delete \
            "${repo_dir}/${relative}/" \
            "${tmp_dir}/previous/${relative}/")"
        if [[ -n "${previous_diff}" ]]; then
            echo "Encrypted archive differs from plaintext source ${relative}:" >&2
            echo "${previous_diff}" >&2
            exit 5
        fi
    fi
done

echo "Alice identity and both encrypted continuity archives verified exactly."
echo "All temporary plaintext was removed on exit."
