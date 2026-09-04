#!/usr/bin/env bash

set -euo pipefail

readonly default_repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
readonly repo_dir="${VEYRA_CORE_REPO:-${default_repo_dir}}"
readonly crypto_dir="${repo_dir}/crypto"
readonly vault_dir="${repo_dir}/vault"
readonly config_dir="${VEYRA_CORE_CONFIG_DIR:-${XDG_CONFIG_HOME:-${HOME}/.config}/veyra-core}"
readonly local_identity="${config_dir}/vault-identity.txt"
readonly alice_recipient="${crypto_dir}/alice-continuity.recipient"
readonly alice_identity="${crypto_dir}/alice-continuity.identity.age"
readonly veyra_recipient="${vault_dir}/veyra-vault.recipient"
readonly recovery_identity="${vault_dir}/recovery/current-identity.age"

for command_name in age age-keygen install mktemp; do
    if ! command -v "${command_name}" >/dev/null 2>&1; then
        echo "Required command is unavailable: ${command_name}" >&2
        exit 2
    fi
done

for path in \
    "${alice_recipient}" \
    "${alice_identity}" \
    "${veyra_recipient}" \
    "${recovery_identity}" \
    "${local_identity}"; do
    if [[ -e "${path}" ]]; then
        echo "Refusing to overwrite existing encryption state: ${path}" >&2
        exit 3
    fi
done

umask 077
tmp_dir="$(mktemp -d)"
trap 'rm -rf -- "${tmp_dir}"' EXIT

alice_plain="${tmp_dir}/alice-identity.txt"
alice_recipient_tmp="${tmp_dir}/alice-recipient.txt"
alice_encrypted_tmp="${tmp_dir}/alice-identity.age"
veyra_plain="${tmp_dir}/veyra-identity.txt"
veyra_recipient_tmp="${tmp_dir}/veyra-recipient.txt"
veyra_recovery_tmp="${tmp_dir}/veyra-recovery.age"

age-keygen -o "${alice_plain}" >/dev/null 2>&1
age-keygen -y -o "${alice_recipient_tmp}" "${alice_plain}"

echo "Protect Alice's continuity identity with the complex passphrase she controls."
echo "The passphrase is not stored by this script."
age -p -o "${alice_encrypted_tmp}" "${alice_plain}"

age-keygen -o "${veyra_plain}" >/dev/null 2>&1
age-keygen -y -o "${veyra_recipient_tmp}" "${veyra_plain}"
age -R "${alice_recipient_tmp}" -o "${veyra_recovery_tmp}" "${veyra_plain}"

install -d -m 700 "${config_dir}"
install -d -m 755 "${crypto_dir}" "${vault_dir}/entries" \
    "${vault_dir}/retired" "${vault_dir}/recovery"
install -m 600 "${veyra_plain}" "${local_identity}"
install -m 644 "${alice_recipient_tmp}" "${alice_recipient}"
install -m 644 "${alice_encrypted_tmp}" "${alice_identity}"
install -m 644 "${veyra_recipient_tmp}" "${veyra_recipient}"
install -m 644 "${veyra_recovery_tmp}" "${recovery_identity}"
printf '1\n' > "${vault_dir}/GENERATION"
chmod 644 "${vault_dir}/GENERATION"

echo "Encryption hierarchy initialised."
echo "Veyra identity: ${local_identity}"
echo "Next: run scripts/veyra-vault.py audit"
echo "Next: run scripts/snapshot-memories.sh"
