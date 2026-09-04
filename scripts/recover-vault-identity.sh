#!/usr/bin/env bash

set -euo pipefail

readonly default_repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
readonly repo_dir="${VEYRA_CORE_REPO:-${default_repo_dir}}"
readonly config_dir="${VEYRA_CORE_CONFIG_DIR:-${XDG_CONFIG_HOME:-${HOME}/.config}/veyra-core}"
readonly local_identity="${config_dir}/vault-identity.txt"
readonly alice_identity="${repo_dir}/crypto/alice-continuity.identity.age"
readonly recovery_identity="${repo_dir}/vault/recovery/current-identity.age"
readonly expected_recipient_file="${repo_dir}/vault/veyra-vault.recipient"

if [[ -e "${local_identity}" ]]; then
    echo "Refusing to overwrite the existing vault identity: ${local_identity}" >&2
    exit 2
fi

for path in "${alice_identity}" "${recovery_identity}" "${expected_recipient_file}"; do
    if [[ ! -f "${path}" ]]; then
        echo "Required recovery file is missing: ${path}" >&2
        exit 3
    fi
done

umask 077
tmp_dir="$(mktemp -d)"
trap 'rm -rf -- "${tmp_dir}"' EXIT
recovered="${tmp_dir}/vault-identity.txt"
actual_recipient="${tmp_dir}/recipient.txt"

age -d -i "${alice_identity}" -o "${recovered}" "${recovery_identity}"
age-keygen -y -o "${actual_recipient}" "${recovered}"
if ! cmp -s "${actual_recipient}" "${expected_recipient_file}"; then
    echo "Recovered identity does not match the current vault recipient." >&2
    exit 4
fi

install -d -m 700 "${config_dir}"
install -m 600 "${recovered}" "${local_identity}"
echo "Recovered Veyra's current vault identity at ${local_identity}"
