# Vendored copy of backs-aios-skills

This directory mirrors `skills/` from the public upstream pack so Hydra's
skill spine can discover the skills offline.

- Upstream: https://github.com/Tcuzzo/backs-aios-skills
- Pinned commit: a7671363193f13d5e7c246c12687cd463cfd2784
- Local-only addition: `hive-clone-boot/` (Hydra's public hive profiles; not in upstream).

To refresh: copy the upstream `skills/` tree at the new commit over this
directory, keep `hive-clone-boot/`, and update the pinned commit above.
Do not hand-edit mirrored skills here; fix them upstream and re-sync.
