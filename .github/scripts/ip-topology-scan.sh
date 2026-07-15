#!/usr/bin/env bash
#
# ip-topology-scan.sh — CI guard against leaking private LAN / topology IPs.
#
# Why this exists: this repo's CI uses gitleaks to catch secrets and tokens,
# but gitleaks does NOT flag IP addresses. A public repository must never ship
# a maintainer's real private-network host addresses (RFC1918 / link-local),
# which would expose internal topology. This scan fails CI if any tracked file
# contains such a host IP, EXCEPT for an explicit allowlist of canonical values
# the project legitimately needs:
#
#   * loopback / unspecified          127.0.0.1  0.0.0.0
#   * SSRF test vectors (RFC1918)     192.168.0.1  10.0.0.1  172.16.0.1
#   * cloud-metadata SSRF vector      169.254.169.254
#   * link-local network base         169.254.0.0
#   * RFC5737 documentation ranges    198.51.100.0/24  203.0.113.0/24  192.0.2.0/24
#
# Detected (fail) ranges: RFC1918 (10/8, 172.16/12, 192.168/16) and link-local
# (169.254/16) host addresses that are NOT on the allowlist. NOTE: range bases are
# written in CIDR shorthand on purpose so this script scans clean against itself —
# it is subject to its own rule, with no self-exclusion blind spot.
#
# Run locally:  bash .github/scripts/ip-topology-scan.sh
# Exit 0 = clean, exit 1 = a private host IP leaked into a tracked file.

set -euo pipefail

cd "$(git rev-parse --show-toplevel)"

# Every dotted-quad in tracked text files, as "path:line:ip" (one match per line).
candidates="$(git grep -I -nEo '([0-9]{1,3}\.){3}[0-9]{1,3}' -- . || true)"

findings="$(printf '%s\n' "$candidates" | awk -F: '
  BEGIN {
    # Exact IPs the project legitimately ships (SSRF/CIDR test vectors, loopback).
    split("127.0.0.1 0.0.0.0 192.168.0.1 10.0.0.1 172.16.0.1 169.254.169.254 169.254.0.0", A, " ");
    for (i in A) allow[A[i]] = 1;
  }
  # git grep output is path:line:ip; the ip is the last field and holds no colon.
  NF >= 3 {
    ip = $NF;
    if (ip in allow) next;
    # RFC5737 documentation ranges are public-shaped and never real infra.
    if (ip ~ /^198\.51\.100\./ || ip ~ /^203\.0\.113\./ || ip ~ /^192\.0\.2\./) next;
    if (split(ip, o, ".") != 4) next;
    ok = 1;
    for (k = 1; k <= 4; k++) if (o[k] + 0 > 255) ok = 0;
    if (!ok) next;                      # not a valid IPv4 literal
    a1 = o[1] + 0; a2 = o[2] + 0;
    priv = 0;
    if (a1 == 10) priv = 1;                                   # 10/8
    else if (a1 == 172 && a2 >= 16 && a2 <= 31) priv = 1;     # 172.16/12
    else if (a1 == 192 && a2 == 168) priv = 1;                # 192.168/16
    else if (a1 == 169 && a2 == 254) priv = 1;                # 169.254/16 link-local
    if (priv) print $0;
  }
')"

if [ -n "$findings" ]; then
  echo "ERROR: private LAN / topology IP address(es) found in tracked files."
  echo "gitleaks does not catch IPs; a public repo must not ship real private hosts."
  echo "If a value is a legitimate test vector, add it to the allowlist in this script."
  echo
  printf '%s\n' "$findings"
  exit 1
fi

echo "clean: no private LAN / topology host IPs in tracked files"
exit 0
