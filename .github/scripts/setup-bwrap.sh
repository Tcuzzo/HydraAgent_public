#!/usr/bin/env bash
# Scope namespace permission to an administrator-owned helper on restricted Ubuntu hosts.
set -euo pipefail

sudo apt-get update
sudo apt-get install -y bubblewrap
if [ "$(cat /proc/sys/kernel/apparmor_restrict_unprivileged_userns 2>/dev/null || echo 0)" = 1 ]; then
  sudo install -D -m 0755 /usr/bin/bwrap /usr/local/libexec/hydra-ci/bwrap
  sudo tee /etc/apparmor.d/hydra-ci-bwrap >/dev/null <<'PROFILE'
abi <abi/4.0>,
include <tunables/global>
/usr/local/libexec/hydra-ci/bwrap flags=(default_allow) {
  userns,
}
PROFILE
  sudo apparmor_parser -r /etc/apparmor.d/hydra-ci-bwrap
  export PATH="/usr/local/libexec/hydra-ci:$PATH"
  if [ -n "${GITHUB_PATH:-}" ]; then
    printf '%s\n' /usr/local/libexec/hydra-ci >> "$GITHUB_PATH"
  fi
fi
bwrap --unshare-net --ro-bind /usr /usr --ro-bind /bin /bin \
  --ro-bind-try /lib /lib --ro-bind-try /lib64 /lib64 \
  --proc /proc --dev /dev --die-with-parent --new-session /usr/bin/true
bwrap --version
