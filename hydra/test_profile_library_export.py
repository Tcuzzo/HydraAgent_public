"""A public profile export must remain usable by its bundled runtime."""
import json
from pathlib import Path

import pytest

from hydra.public_profiles import PublicProfileError, export_profiles


def test_unknown_bundled_library_refuses_export_before_writing(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    data = json.loads((Path(__file__).parent / "runtime_data/specialists/seam_bot.json").read_text())
    data["library"] = ["nonexistent-public-playbook"]
    (source / "seam_bot.json").write_text(json.dumps(data))
    destination = tmp_path / "export"
    with pytest.raises(PublicProfileError, match="library"):
        export_profiles(source, destination, ["seam_bot"])
    assert not destination.exists()
