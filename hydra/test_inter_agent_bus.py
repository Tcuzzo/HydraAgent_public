"""Read side of the inter-agent bus — lets parallel agents talk to each other.

log_message() already appends envelopes to .hydraAgent/inter_agent_bus.jsonl.
read_bus() is the missing reader so a running agent can see what its peers
(same trace cohort) have said.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from hydra.inter_agent import create_message, log_message, read_bus, use_trace_id


def _msg(from_role, to_role, action, *, trace_id, mtype="request"):
    with use_trace_id(trace_id):
        return create_message(
            from_role=from_role, to_role=to_role, message_type=mtype, action=action
        )


def test_read_bus_empty_when_no_bus(tmp_path):
    assert read_bus(tmp_path) == []


def test_read_bus_returns_logged_messages(tmp_path):
    log_message(tmp_path, _msg("planner", "doer", "do-x", trace_id="t1"))
    log_message(tmp_path, _msg("doer", "planner", "did-x", trace_id="t1", mtype="response"))
    records = read_bus(tmp_path)
    assert len(records) == 2
    assert records[0]["from"] == "planner"
    assert records[1]["type"] == "response"


def test_read_bus_filters_by_trace_cohort(tmp_path):
    log_message(tmp_path, _msg("planner", "doer", "cohort-A", trace_id="A"))
    log_message(tmp_path, _msg("planner", "auditor", "cohort-B", trace_id="B"))
    a = read_bus(tmp_path, trace_id="A")
    assert len(a) == 1
    assert a[0]["trace_id"] == "A"


def test_read_bus_filters_by_recipient_and_broadcast(tmp_path):
    log_message(tmp_path, _msg("planner", "doer", "to-doer", trace_id="t"))
    log_message(tmp_path, _msg("planner", "broadcast", "to-all", trace_id="t", mtype="broadcast"))
    to_doer = read_bus(tmp_path, to_role="doer")
    actions = {r["message"]["payload"]["action"] for r in to_doer}
    # A doer sees messages addressed to it AND broadcasts.
    assert "to-doer" in actions
    assert "to-all" in actions


def test_read_bus_limit_keeps_most_recent(tmp_path):
    for i in range(5):
        log_message(tmp_path, _msg("planner", "doer", f"m{i}", trace_id="t"))
    last_two = read_bus(tmp_path, limit=2)
    assert len(last_two) == 2
    assert last_two[-1]["message"]["payload"]["action"] == "m4"
