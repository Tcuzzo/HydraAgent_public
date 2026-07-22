# NOTICE

Hydra (public edition) — Copyright (c) 2026 Tcuzzo.
Licensed under the MIT License (see [LICENSE.md](LICENSE.md)) — free for any use,
including commercial.

This product includes third-party software under permissive licenses. None of the
dependencies below is copyleft (no GPL/AGPL/LGPL/MPL), so they do not constrain the
licensing of this project.

## Bundled binary

- **sqlite-vec** (`hydra/_vendor/sqlite_vec/vec0.so`) — vector-search extension for
  SQLite by Alex Garcia, dual-licensed MIT / Apache-2.0.
  - Bundled version: **v0.1.9**
  - SHA-256: `5923730861b86c707cca5602b5f91092f9e52a46706dbc6e269fd4bb9c4498e8`
  - Platform: Linux x86-64 ELF (built with GCC 11.4.0, Ubuntu 22.04). On other
    platforms install the pip wheel (`pip install sqlite-vec`) — the runtime prefers
    the wheel and degrades to keyword (FTS5) recall if no extension is available.
  - Upstream: https://github.com/asg017/sqlite-vec

## Python dependencies (all permissive)

- `jsonschema` — MIT
- `prompt_toolkit` — BSD-3-Clause
- `psutil` — BSD-3-Clause
- `pyfiglet` — MIT
- `pyyaml` — MIT
- `packaging` — Apache-2.0 / BSD-2-Clause (dual)
- `qrcode` — BSD-3-Clause
- `rich` — MIT
- `textual` — MIT
- `sqlite-vec` (optional) — MIT / Apache-2.0
- `pynvml` (optional) — BSD-3-Clause
- `redis` (optional, used opportunistically if present) — MIT
- `playwright` (optional, for the browser tools) — Apache-2.0

Each dependency is the property of its respective authors and is used under its own
license.

## Provenance / "studied, not copied"

Several primitives in Hydra were designed by studying public prior art and then
re-implemented natively — they are this project's own code, not vendored copies of
any third-party agent framework. No runtime source from other agent projects is
included in this repository. See [PROVENANCE.md](PROVENANCE.md) for the derivation
hash of the private original this public edition was extracted from.
