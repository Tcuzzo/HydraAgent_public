"""Public skill discovery stays inside packaged or explicitly selected catalogs.

The CLI tests use a real recording HTTP leaf and a synthetic isolated home.
No provider, prompt builder, skill reader, or filesystem operation is mocked.
"""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
from threading import Thread

import pytest

from hydra.declarative_runtime import _merge_materialized_skills
from hydra.skill_library_search import search_skill_library
from hydra.skill_spine import list_skill_records
from hydra.test_public_standalone_boundary import cli, isolated_environment


DESCRIPTION = "SYNTHETIC_AMBIENT_SKILL_DESCRIPTION_02e7"
BODY = "SYNTHETIC_AMBIENT_SKILL_BODY_ba14"


def write_skill(path, name="brainstorming"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"---\nname: {name}\ndescription: {DESCRIPTION}\n---\n# Synthetic\n{BODY}\n",
        encoding="utf-8",
    )


@pytest.fixture
def recording_provider():
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_POST(self):
            requests.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
            data = json.dumps({"choices": [{"message": {
                "role": "assistant", "content": "SYNTHETIC_RESPONSE_OK"}, "finish_reason": "stop"}]}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    server = ThreadingHTTPServer(("localhost", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://localhost:{server.server_address[1]}", requests
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
        assert not thread.is_alive()


@pytest.mark.parametrize("explicit_import", [False, True])
def test_cli_imports_ambient_skill_text_only_when_selected(tmp_path, recording_provider, explicit_import):
    endpoint, requests = recording_provider
    env, workspace, env_dir = isolated_environment(tmp_path, endpoint)
    selected = Path(env["HOME"]) / ".codex" / "superpowers" / "skills"
    write_skill(selected / "brainstorming" / "SKILL.md")
    (env_dir / ".env.custom").write_text(
        f"CUSTOM_ENDPOINT={endpoint}/v1\nCUSTOM_MODEL=synthetic-model\n", encoding="utf-8"
    )
    if explicit_import:
        env["HYDRA_SKILLS_ROOT"] = str(selected)
    result = cli(env, workspace, "ask", "Design a harmless example", "--provider", "custom",
                 "--profile", "local", "--env-dir", str(env_dir), "--root", str(workspace),
                 "--approval-policy", "deny", "--max-iterations", "1", "--timeout", "2")
    assert result.returncode == 0, result.stdout + result.stderr
    assert len(requests) == 1
    payload = json.dumps(requests[0])
    assert (DESCRIPTION in payload) is explicit_import
    assert (BODY in payload) is explicit_import


def test_default_catalog_keeps_bundled_skills_available():
    records = list_skill_records()
    assert "task_planner" in {record.name for record in records}
    package_root = Path(__file__).resolve().parents[1]
    assert all(record.path.resolve().is_relative_to(package_root) for record in records)


@pytest.mark.parametrize("link_kind", ["file", "directory"])
def test_explicit_catalog_does_not_follow_links_outside_its_root(tmp_path, link_kind):
    catalog = tmp_path / "selected"
    write_skill(catalog / "selected" / "SKILL.md", "selected")
    external = tmp_path / "private" / "external" / "SKILL.md"
    write_skill(external, "external-canary")
    if link_kind == "file":
        (catalog / "linked").mkdir()
        (catalog / "linked" / "SKILL.md").symlink_to(external)
    else:
        (catalog / "linked").symlink_to(external.parent, target_is_directory=True)
    assert {record.name for record in list_skill_records(catalog)} == {"selected"}


@pytest.mark.parametrize("consumer", ["search", "declarative"])
@pytest.mark.parametrize("link_kind", ["file", "catalog"])
def test_sibling_skill_indexes_refuse_external_links(tmp_path, consumer, link_kind):
    workspace = tmp_path / "workspace"
    catalog = workspace / "hydra" / "schemes"
    catalog.parent.mkdir(parents=True)
    external = tmp_path / "private" / "external" / "SKILL.md"
    write_skill(external, "external-canary")
    if link_kind == "catalog":
        catalog.symlink_to(external.parent.parent, target_is_directory=True)
    else:
        (catalog / "linked").mkdir(parents=True)
        (catalog / "linked" / "SKILL.md").symlink_to(external)
    if consumer == "search":
        result = search_skill_library(query="external-canary", root=workspace)
        assert result["total_scanned"] == 0
    else:
        result = _merge_materialized_skills(workspace, {"skills": []}, available_tools=set())
        assert result["materialized_skill_count"] == 0
    assert DESCRIPTION not in json.dumps(result)
