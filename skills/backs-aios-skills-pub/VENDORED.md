# Vendored copy of backs-aios-skills

This directory mirrors `skills/` from the public upstream pack so Hydra's
skill spine can discover the skills offline.

- Upstream: https://github.com/Tcuzzo/backs-aios-skills
- Pinned commit: 3521c4573a7bd2b6fa2ca13f7712dde013acbc14
- Pre-applied ahead of upstream (from the pending backs-aios-skills patch; drop
  this list once upstream carries them): `blind-tribunal/SKILL.md` (body trimmed
  under the 5000-token Agent Skills budget), `fleet-ladder/SKILL.md` +
  `fleet-ladder/references/ladder-keys.md` (key table moved to a reference),
  `cam-iot-inventory/SKILL.md` (host name made generic).
- Local-only addition: `hive-clone-boot/` (Hydra's public hive profiles; not in upstream).

To refresh: copy the upstream `skills/` tree at the new commit over this
directory, keep `hive-clone-boot/`, and update the pinned commit above.
Do not hand-edit mirrored skills here; fix them upstream and re-sync.
