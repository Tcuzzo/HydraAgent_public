# Vendored copy of backs-aios-skills

This directory mirrors `skills/` from the public upstream pack so Hydra's
skill spine can discover the skills offline.

- Upstream: https://github.com/Tcuzzo/backs-aios-skills
- Pinned commit: a7671363193f13d5e7c246c12687cd463cfd2784
- Local-only addition: `hive-clone-boot/` (Hydra's public hive profiles; not in upstream).

To refresh: copy the upstream `skills/` tree at the new commit over this
directory, keep `hive-clone-boot/`, and update the pinned commit above.
Do not hand-edit mirrored skills here; fix them upstream and re-sync.

Local public adaptations (preserve when refreshing and carry upstream where appropriate):

- `hive-clone-boot/` contains generic public profiles.
- `cam-iot-inventory`, `backs-pack-sync`, `fleet-ladder`, `fleet-dispatch`,
  `wayfinder`, `turn-and-burn`, `optimus`, `architecture-engineer`, and the
  observation skills (`co-health`, `conscientious-objector`, `osha-bot`) retain portable methodology while removing private
  serving-code, API, deployment and promotion bindings. Public Hydra uses only
  its configured local or direct-cloud transports.
- `hydra/test_public_scrub_guard.py` guards against reintroducing private literals.

These adaptations do not change upstream ownership or license terms. A refresh
must retain the standalone boundary rather than restore private instructions.
