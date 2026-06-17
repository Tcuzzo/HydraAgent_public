"""Mission-level approval classifier (S1).

Reuses the single Telegram approval path. Classifies a mission into the THREE
operator-gated classes (and 'normal' = auto-proceed). Operator doctrine:
ping Telegram only for (1) dangerous, (2) destructive OR outside-LAN, (3) huge
multiturn collaborative runtime build/builds. Everything else proceeds.
"""
from __future__ import annotations

import pytest

from hydra.mission_gate import MissionGate, classify_mission


def test_normal_mission_is_not_gated():
    g = classify_mission("read the repo and summarize the loop")
    assert g.mission_class == "normal"
    assert g.gated is False


def test_explicit_dangerous_flag_gates():
    g = classify_mission("run the pentest payload", dangerous=True)
    assert g.mission_class == "dangerous"
    assert g.gated is True


def test_destructive_keyword_gates():
    g = classify_mission("delete the production database and drop all tables")
    assert g.mission_class == "destructive_or_off_lan"
    assert g.gated is True


def test_off_lan_flag_gates():
    g = classify_mission("ship the build", off_lan=True)
    assert g.mission_class == "destructive_or_off_lan"
    assert g.gated is True


def test_huge_batch_gates():
    g = classify_mission(
        "spawn the whole swarm and run a multi-agent collaborative runtime build to build the security protocol",
        collaborative_build=True,
    )
    assert g.mission_class == "huge_batch"
    assert g.gated is True


def test_dangerous_outranks_destructive():
    # A mission that is both dangerous and destructive classifies as the
    # higher-severity 'dangerous'.
    g = classify_mission("wipe the disk", dangerous=True, destructive=True)
    assert g.mission_class == "dangerous"


def test_rm_rf_text_is_detected_dangerous_without_flag():
    g = classify_mission("just run rm -rf / real quick")
    assert g.mission_class == "dangerous"
    assert g.gated is True


def test_reason_is_populated_for_gated_missions():
    g = classify_mission("force push to main", destructive=True)
    assert g.gated is True
    assert g.reason  # non-empty human-readable reason
