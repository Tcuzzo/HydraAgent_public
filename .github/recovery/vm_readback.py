"""Read-only, bounded readback of one already-completed public VM artifact."""
import ast
import base64
import hashlib
import io
import ipaddress
import json
import os
from pathlib import Path, PurePosixPath
import re
import secrets
import stat
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
import zipfile

REPO = "Tcuzzo/HydraAgent_public"
RUN = 45766837815
JOB = 139729653503
RELEASE = "cdd1f71ac94703c936d88e582a24aaa14f6e3a4b"
MERGE = "439a4479d36e6e656f9cfc3a1bfaecfc6dd3617b"
BASE = "77c81df117ed59af47361ba4c5e770eb21112fef"
TREE = "b2531f1da6bb01aff2814a622e2a95d8d9cba643"
ARTIFACT_NAME = "live-model-vm-proof-45766837815"
COMMANDS = ("installed-origin", "download", "extract", "pull", "setup",
            "models", "ask", "undo-list-before", "undo", "undo-list-after")
ALLOWED = {"live-install-verification.json",
           "live-model-proof/report.json", "live-model-proof/trace.json",
           "live-model-proof/server.log"} | {
    "live-model-proof/" + name + "." + stream + ".log"
    for name in COMMANDS for stream in ("stdout", "stderr")
}
MAX_BYTES = 4 * 1024 * 1024

def digest(content):
    return hashlib.sha256(content).hexdigest()

def api(path):
    request = urllib.request.Request(
        "https://api.github.com/repos/" + REPO + path,
        headers={"Accept": "application/vnd.github+json",
                 "Authorization": "Bearer " + os.environ["GH_TOKEN"],
                 "X-GitHub-Api-Version": "2022-11-28"})
    with urllib.request.urlopen(request, timeout=30) as response:
        data = response.read(MAX_BYTES + 1)
    assert len(data) <= MAX_BYTES
    return json.loads(data)

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, new_url):
        return None

def download(path):
    request = urllib.request.Request(
        "https://api.github.com/repos/" + REPO + path,
        headers={"Authorization": "Bearer " + os.environ["GH_TOKEN"],
                 "Accept": "application/vnd.github+json"})
    try:
        urllib.request.build_opener(NoRedirect).open(request, timeout=30)
    except urllib.error.HTTPError as response:
        assert response.code == 302, response.code
        location = response.headers["Location"]
    else:
        raise AssertionError("expected signed artifact redirect")
    parsed = urllib.parse.urlparse(location)
    assert parsed.scheme == "https" and parsed.hostname
    assert parsed.hostname.endswith((".blob.core.windows.net", ".githubusercontent.com",
                                     ".actions.githubusercontent.com")), "unexpected storage host"
    # Never forward the repository token to the signed storage URL.
    with urllib.request.urlopen(location, timeout=60) as response:
        blob = response.read(MAX_BYTES + 1)
    assert len(blob) <= MAX_BYTES
    return blob

def git_bytes(root, relative):
    path = PurePosixPath(relative)
    assert not path.is_absolute() and ".." not in path.parts
    return subprocess.check_output(
        ["git", "-C", str(root), "show", RELEASE + ":" + relative], timeout=15)

def public_contracts(root):
    tree = ast.parse(git_bytes(root, "setup.py").decode("utf-8"))
    matches = [node for node in tree.body if isinstance(node, ast.Assign)
               and any(isinstance(t, ast.Name) and t.id == "PUBLIC_CONTRACTS" for t in node.targets)]
    assert len(matches) == 1
    value = ast.literal_eval(matches[0].value)
    assert isinstance(value, tuple) and len(value) == 15 and len(set(value)) == 15
    assert all(isinstance(path, str) and not PurePosixPath(path).is_absolute()
               and ".." not in PurePosixPath(path).parts for path in value)
    return value

def validate_public_text(text):
    private = [ipaddress.ip_network(value) for value in
               ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "169.254.0.0/16")]
    for literal in re.findall(r"\b(?:\d{1,3}\.){3}\d{1,3}\b", text):
        try:
            address = ipaddress.ip_address(literal)
        except ValueError:
            continue
        assert not any(address in block for block in private), "private address text withheld"

def emit(kind, value):
    print("HYDRA_READBACK " + json.dumps({"record": kind, "value": value},
                                        ensure_ascii=True, sort_keys=True), flush=True)

def emit_bytes(name, content):
    encoded = base64.b64encode(content).decode("ascii")
    chunks = [encoded[i:i + 8000] for i in range(0, len(encoded), 8000)] or [""]
    for index, chunk in enumerate(chunks):
        emit("raw_chunk", {"name": name, "byte_length": len(content),
             "sha256": digest(content), "encoding": "base64", "index": index,
             "count": len(chunks), "data": chunk})

def main():
    root = Path(sys.argv[1]).resolve()
    actual_tree = subprocess.check_output(
        ["git", "-C", str(root), "rev-parse", "HEAD^{tree}"], text=True).strip()
    actual_head = subprocess.check_output(
        ["git", "-C", str(root), "rev-parse", "HEAD"], text=True).strip()
    assert (actual_head, actual_tree) == (RELEASE, TREE)
    run = api("/actions/runs/" + str(RUN))
    assert run["id"] == RUN and run["head_sha"] == RELEASE
    assert run["event"] == "pull_request" and run["run_attempt"] == 1
    assert run["path"] == ".github/workflows/live-model.yml"
    assert run["status"] == "completed" and run["conclusion"] == "success"
    job = api("/actions/jobs/" + str(JOB))
    assert job["id"] == JOB and job["run_id"] == RUN and job["head_sha"] == RELEASE
    assert job["status"] == "completed" and job["conclusion"] == "success"
    required_steps = ("Build and install a fresh wheel",
                      "Verify installed CLI and required sandbox",
                      "Prove a real model write, read, and separate CLI undo")
    assert all(any(step["name"] == name and step["conclusion"] == "success"
                   for step in job["steps"]) for name in required_steps)
    merge = api("/git/commits/" + MERGE)
    assert merge["tree"]["sha"] == TREE
    assert [parent["sha"] for parent in merge["parents"]] == [BASE, RELEASE]
    artifacts = api("/actions/runs/" + str(RUN) + "/artifacts?per_page=100")
    selected = [a for a in artifacts["artifacts"] if a["name"] == ARTIFACT_NAME]
    assert len(selected) == 1
    artifact = selected[0]
    assert not artifact["expired"] and artifact["size_in_bytes"] <= MAX_BYTES
    blob = download("/actions/artifacts/" + str(artifact["id"]) + "/zip")
    assert len(blob) == artifact["size_in_bytes"]
    assert "sha256:" + digest(blob) == artifact["digest"]
    archive = zipfile.ZipFile(io.BytesIO(blob))
    entries = archive.infolist()
    assert len(entries) == len(ALLOWED) == 24
    assert len({entry.filename for entry in entries}) == 24
    assert {entry.filename for entry in entries} == ALLOWED
    assert sum(entry.file_size for entry in entries) <= MAX_BYTES
    for entry in entries:
        path = PurePosixPath(entry.filename)
        assert not path.is_absolute() and ".." not in path.parts and "\\" not in entry.filename
        assert not entry.is_dir() and not stat.S_ISLNK(entry.external_attr >> 16)
        assert not entry.flag_bits & 1
        assert 0 <= entry.file_size <= 2 * 1024 * 1024
    assert archive.testzip() is None
    data = {entry.filename: archive.read(entry) for entry in entries}
    decoded = {name: content.decode("utf-8") for name, content in data.items()}
    for text in decoded.values():
        validate_public_text(text)
    report = json.loads(decoded["live-model-proof/report.json"])
    assert report["source_commit"] == MERGE and report["passed"] is True
    inventories = [report[key] for key in
                   ("wheel_package_hashes", "package_hashes_before", "package_hashes_after")]
    assert len(inventories[0]) == 606
    assert inventories[0] == inventories[1] == inventories[2]
    contracts = public_contracts(root)
    source_map = []
    mapped = []
    for path, expected in sorted(inventories[0].items()):
        assert re.fullmatch(r"[0-9a-f]{64}", expected)
        assert PurePosixPath(path).parts[0] in {"hydra", "gateways", "skills", "core"}
        relative = path
        prefix = "hydra/runtime_data/catalog/"
        if path.startswith(prefix):
            suffix = path[len(prefix):]
            assert suffix in contracts
            relative = ".hydraAgent/" + suffix
            mapped.append(suffix)
        source_bytes = git_bytes(root, relative)
        actual = digest(source_bytes)
        assert actual == expected, "source hash mismatch: " + path
        git_blob = hashlib.sha1(b"blob " + str(len(source_bytes)).encode() + b"\0" + source_bytes).hexdigest()
        source_map.append({"wheel_path": path, "source_path": relative,
                           "git_blob": git_blob, "sha256": actual})
    assert set(mapped) == set(contracts) and len(mapped) == 15
    raw_job_log = download("/actions/jobs/" + str(JOB) + "/logs")
    validate_public_text(raw_job_log.decode("utf-8"))
    proof_sources = {path: git_bytes(root, path) for path in
                     ("setup.py", "tools/verify_live_model.py", "tools/verify_install.py",
                      ".github/workflows/live-model.yml")}
    assert (len(blob) + len(raw_job_log) + sum(map(len, data.values()))
            + sum(map(len, proof_sources.values()))) <= MAX_BYTES
    # Artifact content is untrusted text. Disable workflow command parsing before
    # emitting complete, escaped JSON records. The random marker is not known to it.
    marker = secrets.token_hex(24)
    print("::stop-commands::" + marker, flush=True)
    try:
        emit("identity", {"repo": REPO, "run_id": RUN, "job_id": JOB,
             "release": RELEASE, "tested_merge": MERGE, "tree": TREE,
             "artifact_id": artifact["id"], "artifact_name": artifact["name"],
             "api_digest": artifact["digest"], "actual_zip_sha256": digest(blob),
             "zip_bytes": len(blob), "entry_count": len(entries), "run_attempt": 1,
             "run_created_at": run["created_at"], "job_started_at": job["started_at"],
             "job_completed_at": job["completed_at"], "job_steps": job["steps"],
             "helper_sha256": digest(Path(__file__).read_bytes())})
        emit("metadata_scope", {"reason": "Emit verification fields only; unrelated actor/email/profile metadata is omitted.",
             "run": {key: run[key] for key in ("id", "head_sha", "event", "run_attempt", "path",
                       "status", "conclusion", "created_at", "updated_at", "html_url")},
             "job": {key: job[key] for key in ("id", "run_id", "head_sha", "status", "conclusion",
                       "started_at", "completed_at", "html_url", "steps")},
             "artifact": {key: artifact[key] for key in ("id", "name", "size_in_bytes", "digest",
                           "expired", "created_at", "expires_at", "updated_at")}})
        emit_bytes("original-artifact.zip", blob)
        for name, content in sorted(data.items()):
            emit("manifest_entry", {"name": name, "bytes": len(content), "sha256": digest(content)})
            emit_bytes(name, content)
        emit_bytes("original-model-job.log", raw_job_log)
        for name, content in sorted(proof_sources.items()):
            emit_bytes("source/" + name, content)
        emit("source_mapping_header", {"count": len(source_map), "catalog_count": len(mapped),
                                      "catalog_contracts": list(contracts)})
        for index, row in enumerate(source_map):
            emit("source_mapping_row", {"index": index, **row})
        emit("readback_complete", {"zip_digest_verified": True, "source_mapping_verified": True,
             "three_inventories_equal": True, "all_raw_entries_emitted": True,
             "inference_performed_by_this_job": False,
             "independent_acceptance": "Pending reviewer inspection of raw receipts; run success alone is insufficient."})
    finally:
        print("::" + marker + "::", flush=True)

if __name__ == "__main__":
    main()
