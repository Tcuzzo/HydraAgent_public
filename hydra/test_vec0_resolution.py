"""The sqlite-vec wheel returns a suffixless SQLite extension path."""
from types import SimpleNamespace

import pytest

from hydra import unified_memory


@pytest.mark.parametrize("suffix", [".dll", ".so", ".dylib"])
def test_wheel_suffixless_extension_path_is_resolved(monkeypatch, tmp_path, suffix):
    stem = tmp_path / "vec0"
    stem.with_suffix(suffix).write_bytes(b"extension")
    monkeypatch.delenv("HYDRA_VEC0_PATH", raising=False)
    monkeypatch.setattr(unified_memory.importlib, "import_module",
                        lambda name: SimpleNamespace(loadable_path=lambda: str(stem)))
    assert unified_memory.resolve_vec0_extension() == str(stem)


def test_suffixless_operator_extension_override_is_resolved(monkeypatch, tmp_path):
    stem = tmp_path / "custom-vec0"
    stem.with_suffix(".dll").write_bytes(b"extension")
    monkeypatch.setenv("HYDRA_VEC0_PATH", str(stem))
    assert unified_memory.resolve_vec0_extension() == str(stem)
