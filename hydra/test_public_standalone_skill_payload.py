"""Portable public skills must not prescribe unavailable private operations."""
from pathlib import Path
import re

import yaml

from hydra.skill_spine import list_skill_records


PACK = Path(__file__).resolve().parents[1] / "skills/backs-aios-skills-pub"
PRIVATE_OPERATIONS = (
    re.compile(r"backend/services/(?:co_health|conscientious_objector|osha_bot|authority_surface_classifier)\.py"),
    re.compile(r"\bcore\.h_boot\.runtime_boot\b"),
    re.compile(r"/api/(?:router/(?:resolve|complete|outcome)|system/needle_engineer_rungs|kev/decision)\b"),
    re.compile(r"/(?:opt|srv)/[^\s`]*(?:Jarvis|BACKS)[^\s`]*", re.IGNORECASE),
)


def test_real_skill_loader_exposes_no_private_operational_instructions():
    records = list_skill_records(PACK)
    assert records, "public portable skills must remain available"
    required = {"co-health", "conscientious-objector", "osha-bot", "architecture-engineer"}
    assert required <= {record.path.parent.name for record in records}
    violations = []
    for record in records:
        text = record.path.read_text(encoding="utf-8")
        if any(pattern.search(text) for pattern in PRIVATE_OPERATIONS):
            violations.append(record.path.relative_to(PACK).as_posix())
    assert not violations, f"private operational instructions exposed by skill loader: {violations}"


def test_adapted_public_skills_keep_their_license_and_identity():
    records = list_skill_records(PACK)
    selected = [record for record in records if record.path.parent.name in {
        "co-health", "conscientious-objector", "osha-bot", "architecture-engineer"}]
    assert len(selected) == 4
    for record in selected:
        text = record.path.read_text(encoding="utf-8")
        frontmatter = yaml.safe_load(text.split("---", 2)[1])
        assert frontmatter["license"] == "MIT"
        assert record.name == record.path.parent.name
