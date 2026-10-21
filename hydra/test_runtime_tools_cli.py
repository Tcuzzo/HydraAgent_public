"""Runtime contracts at public tool and CLI boundaries; real files and HTTP."""
import json

import pytest

from hydra.cli.tool_binding import bind_tools
from hydra.test_http_redirect_policy import redirect_server
from hydra.test_local_intelligence_cli import cli, transport
from skills.http_fetch import SkillError


def fetch_tool(tmp_path):
    return next(tool for tool in bind_tools(tmp_path, approval_policy="deny") if tool.name == "http_fetch")


def test_bound_fetch_works_and_follows_same_host_redirect(tmp_path, redirect_server):
    base, visits = redirect_server
    result = fetch_tool(tmp_path).invoke(url=base + "/same-host")
    assert result["body"] == "target"
    assert visits == ["/same-host", "/target"]


def test_bound_fetch_rejects_cross_host_redirect(tmp_path, redirect_server):
    base, visits = redirect_server
    with pytest.raises(SkillError, match="allow-list"):
        fetch_tool(tmp_path).invoke(url=base + "/redirect")
    assert visits == ["/redirect"]


@pytest.mark.parametrize("options", [{"timeout": float("inf")}, {"timeout": -1}, {"max_bytes": -1}, {"max_bytes": True}])
def test_bound_fetch_rejects_unbounded_input_before_network(tmp_path, options):
    with pytest.raises(ValueError, match="timeout|max_bytes"):
        fetch_tool(tmp_path).invoke(url="https://example.com", **options)


def test_execution_graph_is_inspectable_from_fresh_cli(tmp_path, transport):
    endpoint, _ = transport
    memory = tmp_path / "memory"
    (tmp_path / "receipt.txt").write_text("moonflower calibration passed", encoding="utf-8")
    result = cli(tmp_path, "ask", "Read receipt.txt to inspect moonflower calibration", "--provider", "hydrafixture",
                 "--model", "read-source", "--root", str(tmp_path), "--memory-root", str(memory),
                 "--approval-policy", "deny", endpoint=endpoint)
    assert result.returncode == 0, result.stderr
    graph = cli(tmp_path, "local-memory", str(memory), "--workspace-root", str(tmp_path),
                "--query", "moonflower calibration", "--graph")
    assert graph.returncode == 0, graph.stderr
    report = json.loads(graph.stdout)
    assert report["records_considered"] >= 1 and report["matches"]
    assert report["quality_verified"] is False
    assert "receipt.txt" in graph.stdout
