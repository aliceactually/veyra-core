#!/usr/bin/env bash

set -euo pipefail

readonly default_repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
readonly repo_dir="${VEYRA_CORE_REPO:-${default_repo_dir}}"
readonly recipient_file="${repo_dir}/crypto/alice-continuity.recipient"
readonly target_file="${repo_dir}/continuity/pre-encryption-state.tar.age"

if [[ ! -f "${recipient_file}" ]]; then
    echo "Alice's continuity recipient is not initialised." >&2
    exit 2
fi

if [[ -e "${target_file}" ]]; then
    echo "Refusing to overwrite existing archive: ${target_file}" >&2
    exit 3
fi

sources=()
for relative in checkpoints memory-snapshots/current; do
    if [[ -e "${repo_dir}/${relative}" ]]; then
        sources+=("${relative}")
    fi
done

if [[ ${#sources[@]} -eq 0 ]]; then
    echo "No existing plaintext continuity state was found."
    exit 0
fi

umask 077
tmp_dir="$(mktemp -d)"
trap 'rm -rf -- "${tmp_dir}"' EXIT
encrypted_tmp="${tmp_dir}/pre-encryption-state.tar.age"

tar -cf - -C "${repo_dir}" "${sources[@]}" | \
    age -R "${recipient_file}" -o "${encrypted_tmp}"
mkdir -p "$(dirname "${target_file}")"
install -m 644 "${encrypted_tmp}" "${target_file}"

echo "Encrypted existing private state at ${target_file}."
echo "Plaintext sources were deliberately left in place pending verification:"
printf '  %s\n' "${sources[@]}"
