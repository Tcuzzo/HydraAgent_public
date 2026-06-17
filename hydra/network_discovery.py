"""Network discovery plan rendering for HydraAgent.

This module is preview-only. It parses a CIDR target, enumerates the hosts the
operator *would* probe, classifies the network's visibility (private, public,
loopback, link-local), and renders the exact command templates a future probe
slice would run. No live probing happens here — no socket is opened, no host
is contacted. The operator chooses the final execution path later.
"""
from __future__ import annotations

import ipaddress
from dataclasses import dataclass
from pathlib import Path  # noqa: F401  (kept for symmetry with other ops modules)
from typing import Any

from hydra.ops_packs import OpsPackError, TargetSpec, parse_target


SCHEMA = "hydra.network_discovery_plan.v1"
RISK_TIER = "T2"
DEFAULT_MAX_PREVIEW_HOSTS = 10
DEFAULT_MAX_TOTAL_HOSTS = 65_536  # /16 IPv4 cap; protects operator from runaway preview

_COMMAND_TEMPLATES: list[dict[str, Any]] = [
    {
        "id": "icmp_sweep",
        "kind": "ping",
        "command_template": "ping -c 1 -W 1 {host}",
        "rationale": "one ICMP echo per host with a one-second wait",
    },
    {
        "id": "tcp_top_ports",
        "kind": "tcp_port_check",
        "command_template": (
            "bash -c 'for p in 22 53 80 139 443 445 3306 5432 8080 8443; do "
            "timeout 1 bash -c \"echo > /dev/tcp/{host}/$p\" 2>/dev/null && "
            "echo {host}:$p open; done'"
        ),
        "rationale": "TCP open-port check across a small common-service set",
    },
    {
        "id": "reverse_dns",
        "kind": "dns_lookup",
        "command_template": "getent hosts {host}",
        "rationale": "reverse DNS lookup via NSS",
    },
]


@dataclass(frozen=True)
class NetworkDiscoveryError(Exception):
    message: str

    def __str__(self) -> str:
        return self.message


def plan_discovery(
    target_raw: str,
    *,
    max_preview_hosts: int = DEFAULT_MAX_PREVIEW_HOSTS,
    max_total_hosts: int = DEFAULT_MAX_TOTAL_HOSTS,
) -> dict[str, Any]:
    """Render a preview-only network discovery plan for ``target_raw``.

    ``target_raw`` is a §10.51 target string. Only ``cidr:`` targets are
    accepted in this slice; other target types raise
    :class:`NetworkDiscoveryError` with an explicit ``not implemented`` message.
    The returned plan never includes live results — every entry in
    ``command_plan`` has ``execute: false`` and a ``{host}`` placeholder.
    """
    if max_preview_hosts <= 0:
        raise NetworkDiscoveryError("max_preview_hosts must be a positive integer")
    if max_total_hosts <= 0:
        raise NetworkDiscoveryError("max_total_hosts must be a positive integer")

    try:
        target = parse_target(target_raw)
    except OpsPackError as e:
        raise NetworkDiscoveryError(str(e)) from e

    if target.type != "cidr":
        raise NetworkDiscoveryError(
            f"not implemented: network discovery plan supports cidr targets in "
            f"this slice; got {target.type!r}"
        )

    try:
        network = ipaddress.ip_network(target.value, strict=False)
    except ValueError as e:
        raise NetworkDiscoveryError(f"invalid CIDR {target.value!r}: {e}") from e

    if network.num_addresses > max_total_hosts:
        raise NetworkDiscoveryError(
            f"network {network} has {network.num_addresses} addresses, exceeds "
            f"max_total_hosts={max_total_hosts}; narrow the CIDR for discovery preview"
        )

    hosts = _host_iter(network)
    preview = [str(h) for h in hosts[:max_preview_hosts]]
    classification = _classify_network(network)

    command_plan = [
        {**template, "risk_tier": RISK_TIER, "execute": False}
        for template in _COMMAND_TEMPLATES
    ]

    rot_signals = _scan_rot(network, classification, len(hosts))

    proof = [
        f"target_type={target.type}",
        f"network={network}",
        f"version={network.version}",
        f"total_hosts={len(hosts)}",
        f"preview_hosts={len(preview)}",
        f"command_templates={len(command_plan)}",
        f"executed=false",
    ]

    return {
        "schema": SCHEMA,
        "target": target.to_dict(),
        "network": {
            "cidr": str(network),
            "version": network.version,
            "num_addresses": network.num_addresses,
            "num_hosts": len(hosts),
            "prefixlen": network.prefixlen,
        },
        "classification": classification,
        "risk_tier": RISK_TIER,
        "host_summary": {
            "total_hosts": len(hosts),
            "first_host": str(hosts[0]) if hosts else None,
            "last_host": str(hosts[-1]) if hosts else None,
            "preview_hosts": preview,
            "preview_truncated": len(hosts) > len(preview),
        },
        "command_plan": command_plan,
        "permission_policy": "operator-selected-later",
        "execution_policy": "preview only; this slice does not contact any host",
        "rot_signals": rot_signals,
        "proof": proof,
    }


def render_text(plan: dict[str, Any]) -> str:
    network = plan["network"]
    host_summary = plan["host_summary"]
    lines = [
        f"Hydra network discovery plan: {plan['target']['value']}",
        f"risk_tier: {plan['risk_tier']}  execution_policy: {plan['execution_policy']}",
        f"network: {network['cidr']} ipv{network['version']} prefix=/{network['prefixlen']} "
        f"total_addresses={network['num_addresses']} hosts={network['num_hosts']}",
        f"classification: " + ", ".join(
            f"{k}={v}" for k, v in sorted(plan["classification"].items())
        ),
        f"hosts: first={host_summary['first_host']} last={host_summary['last_host']} "
        f"preview={len(host_summary['preview_hosts'])} "
        f"truncated={host_summary['preview_truncated']}",
        "preview_hosts:",
    ]
    for host in host_summary["preview_hosts"]:
        lines.append(f"  - {host}")
    if not host_summary["preview_hosts"]:
        lines.append("  - none")
    lines.append("command plan (preview only):")
    for command in plan["command_plan"]:
        lines.append(
            f"  - [{command['risk_tier']}] {command['id']} ({command['kind']}): "
            f"{command['command_template']}"
        )
    if plan["rot_signals"]:
        lines.append("rot signals:")
        for signal in plan["rot_signals"]:
            lines.append(f"  - {signal['id']} [{signal['severity']}]: {signal['detail']}")
    lines.append("proof:")
    for p in plan["proof"]:
        lines.append(f"  - {p}")
    return "\n".join(lines) + "\n"


def _host_iter(network: ipaddress._BaseNetwork) -> list[Any]:
    if network.num_addresses <= 2:
        return list(network)
    return list(network.hosts())


def _classify_network(network: ipaddress._BaseNetwork) -> dict[str, bool]:
    return {
        "is_private": bool(network.is_private),
        "is_global": bool(network.is_global),
        "is_loopback": bool(network.is_loopback),
        "is_link_local": bool(network.is_link_local),
        "is_multicast": bool(network.is_multicast),
        "is_reserved": bool(network.is_reserved),
        "is_unspecified": bool(network.is_unspecified),
    }


def _scan_rot(
    network: ipaddress._BaseNetwork,
    classification: dict[str, bool],
    host_count: int,
) -> list[dict[str, str]]:
    signals: list[dict[str, str]] = []
    if classification["is_global"] and not classification["is_private"]:
        signals.append({
            "id": "public_target",
            "severity": "red",
            "detail": (
                "target network is publicly routable; live discovery would touch "
                "third-party hosts and may be illegal without explicit authorization"
            ),
        })
    if classification["is_multicast"] or classification["is_reserved"]:
        signals.append({
            "id": "non_unicast_target",
            "severity": "yellow",
            "detail": "target network is multicast or reserved; standard probes will not behave normally",
        })
    if classification["is_unspecified"]:
        signals.append({
            "id": "unspecified_target",
            "severity": "red",
            "detail": "0.0.0.0/0 or ::/0 covers the entire address space; refuse to probe",
        })
    if host_count == 0:
        signals.append({
            "id": "empty_host_set",
            "severity": "yellow",
            "detail": "network contains no usable hosts",
        })
    return signals
