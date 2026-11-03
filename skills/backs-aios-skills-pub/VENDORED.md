# Vendored copy of backs-aios-skills

This directory mirrors `skills/` from the public upstream pack so Hydra's
skill spine can discover the skills offline.

- Upstream: https://github.com/Tcuzzo/backs-aios-skills
- Pinned commit: ffdd88b9fb5835c5d8b721906e47e40e4c714472
- Local-only addition: `hive-clone-boot/` (Hydra's public hive profiles; not in upstream).

To refresh: copy the upstream `skills/` tree at the new commit over this
directory, keep `hive-clone-boot/`, and update the pinned commit above.
Do not hand-edit mirrored skills here; fix them upstream and re-sync.
