"""Setup must preserve provider boundaries before writing configuration."""
import pytest

from hydra.setup import SetupError, setup_cloud_provider, write_env_file


@pytest.mark.parametrize("provider", ["x/../../escape", "x\\..\\escape", "../bad", "bad\nNAME"])
def test_setup_rejects_provider_paths(tmp_path, provider):
    with pytest.raises(SetupError):
        write_env_file(provider, {"CODEX_MODEL": "m"}, env_dir=tmp_path)
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("key,value", [("CLOUD_MODEL", "m\nCLOUD_ENDPOINT=https://other"), ("CLOUD_MODEL\nINJECTED", "m"), ("CLOUD_MODEL", "m\rOTHER=value")])
def test_setup_rejects_env_injection_before_overwrite(tmp_path, key, value):
    path = tmp_path / ".env.cloud"
    path.write_text("CLOUD_MODEL=original\n", encoding="utf-8")
    with pytest.raises(SetupError):
        write_env_file("cloud", {key: value}, env_dir=tmp_path)
    assert path.read_text() == "CLOUD_MODEL=original\n"


def test_setup_accepts_any_model_family(tmp_path):
    result = setup_cloud_provider("anthropic", endpoint="http://localhost:8000/v1", model="claude-via-proxy", api_key="token", env_dir=tmp_path)
    assert result.path.is_file()


def test_setup_accepts_keyless_compatible_server(tmp_path):
    result = setup_cloud_provider("localserver", endpoint="http://localhost:8000/v1", model="chosen-model", api_key=None, env_dir=tmp_path)
    assert "LOCALSERVER_MODEL=chosen-model" in result.path.read_text()
