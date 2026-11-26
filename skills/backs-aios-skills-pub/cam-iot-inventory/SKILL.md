---
name: cam-iot-inventory
description: "Use when inventorying cameras/IoT/Wi-Fi on an explicitly authorized system — stable device identity, honest scan results, inventory is not a picture. Compose with secure-delivery + live-research. Trigger words: cam inventory, IoT inventory, camera MAC sticky, wifi-scan iface, RTSP unreachable, DHCP churn camera id."
license: MIT
---

# Camera and IoT inventory

This public adaptation provides inventory and verification discipline. Hydra ships no camera console, Wi-Fi scanner, device credentials or private infrastructure adapter. Discover the operator-authorized tools and documented device API before making a network request; if none is available, report that boundary.

## Evidence and identity

- Record only devices within the explicitly authorized scope. Do not infer permission from a local network connection.
- Use observed stable device identifiers where available. A changed address does not by itself establish that a device is new.
- Never invent hardware addresses, wireless interfaces, access points or successful scans.
- An inventory row proves that a device was listed. It does not prove a working stream or a fresh snapshot.
- Preserve timestamp, source and failure state for snapshots. A stale image must not be reported as current.
- Keep real device identities, private addresses, credentials and deployment paths out of public reports and commits.

## Bounded checks

Before a supported live integration is accepted, test address changes for the same device, missing identifiers, unreachable streams, concurrent snapshot requests and missing scan configuration. Use the real documented interface for the authorized system. Missing configuration must produce an honest failure rather than a fabricated empty or successful result.

Record the tested software revision and observed outcomes. Inventory work does not authorize device configuration, deployment or service restart. Reuse [secure-delivery](../secure-delivery/SKILL.md) and [live-research](../live-research/SKILL.md) for the evidence and review boundary.

Public adaptation of the BACKS AIOS camera-inventory methodology; private serving-code and API bindings have been removed. Upstream license and attribution remain in this pack.
