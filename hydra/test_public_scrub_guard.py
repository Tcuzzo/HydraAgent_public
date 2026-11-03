"""Public-edition scrub guard: no operator infrastructure in any tracked file.

This repository is public. It may explain *how* Hydra connects to a host, but it must
never name the maintainer's own machines, home network devices, private repositories or
private deploy paths. Those are replaced by placeholders such as ``<backs-host>``,
``<BACKS_DEPLOY_ROOT>`` and ``<camera-mac>`` (see docs/HOW_HYDRA_WORKS.md).

Checks, each reporting ``file:line`` and the rule (never the matched value):

* operator names - a fixed set of private host, repo and path names. The set is stored
  as SHA-256 digests so this guard does not publish the names it is guarding.
* MAC addresses  - a real device hardware address. Only the IEEE documentation block
  ``00:00:5e:00:53:xx`` (RFC 7042) may appear.
* home paths     - ``/home/<name>`` or ``C:\\Users\\<name>`` for a name that is not a
  generic example (``op``, ``user``, ``owner``, ``you`` ...).

Private LAN IP addresses are covered by ``.github/scripts/ip-topology-scan.sh`` in CI.
"""
from __future__ import annotations

import hashlib
import re
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]

#: sha256(lowercase name) for operator-specific host, repo and path names.
OPERATOR_NAME_DIGESTS = frozenset({
    "de1c9989db81aa551f52599493417ce0e1c0ec2599563d57a1d2f3ab7ec43369",
    "fa9f0efbf505d4e44e9e55042ceed3a0cd578c44561ebc117ec2fbfb15c0dd7b",
    "2ab4d018a13178acc5017a4f8bbc24f4b5753b0eb4116cdc36862238abbb1315",
    "85a60526c1258ff74449c391280b985a5118b5c2ec8dd113d2d91cae7c6d3ee0",
    "aac459a82ff3f481d6f7fad4ed1875003856857b0957d9173414a7f322e280eb",
    "2b4225d095e6d917c877466718fd746739ca7a22688b75f0d74974426f3c23fb",
    "dd4f5866ba704c4a36facc1c0e8fea4aa8c0d31f932336d213ce227f06287933",
    "2f96078225a846a0284a385c42e6c40d890fa1736b9e95c92fd0994066068da0",
    "adecebfb139b24bdcb3157dae230ddbb073c46e3db3729fdc35297850bce1ab7",
    "0fbb9b7b457b424e51fed860eed1609fb9c8cad36da9bba4b66745bad9771092",
})

GENERIC_USERS = frozenset({
    "op", "user", "users", "owner", "you", "me", "runner", "operator", "example",
    "username", "your-user", "your_user", "name", "alice", "bob", "...",
})

_TOKEN = re.compile(r"[A-Za-z0-9_.-]+")
_MAC = re.compile(r"(?<![0-9A-Fa-f:])(?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}(?![0-9A-Fa-f:])")
_DOC_MAC = re.compile(r"^00:00:5e:00:53:[0-9a-f]{2}$", re.I)
_HOME = re.compile(r"/home/([A-Za-z0-9_.-]+)")
_WIN_HOME = re.compile(r"[A-Za-z]:\\{1,2}Users\\{1,2}([^\\/\s'\"`]+)", re.I)


def _tracked_text_files() -> list[Path]:
    try:
        out = subprocess.run(["git", "ls-files", "-z"], cwd=REPO, capture_output=True,
                             check=True, timeout=30).stdout
    except (OSError, subprocess.SubprocessError):
        pytest.skip("not a git checkout; the scrub guard scans tracked files only")
    files = []
    for rel in out.decode("utf-8", "replace").split("\0"):
        path = REPO / rel
        if rel and path.is_file():
            files.append(path)
    return files


def _lines(path: Path):
    data = path.read_bytes()
    if b"\0" in data[:8192]:
        return
    yield from enumerate(data.decode("utf-8", "replace").splitlines(), 1)


def _digest(token: str) -> str:
    return hashlib.sha256(token.lower().encode()).hexdigest()


def _findings() -> list[str]:
    found: list[str] = []
    for path in _tracked_text_files():
        rel = path.relative_to(REPO).as_posix()
        for no, line in _lines(path):
            where = f"{rel}:{no}"
            for tok in _TOKEN.findall(line):
                for part in {tok, tok.strip(".-_")}:
                    if part and _digest(part) in OPERATOR_NAME_DIGESTS:
                        found.append(f"{where}: operator host/repo/path name")
            for mac in _MAC.findall(line):
                if not _DOC_MAC.match(mac):
                    found.append(f"{where}: device MAC address (use <camera-mac> or 00:00:5e:00:53:xx)")
            for rx in (_HOME, _WIN_HOME):
                for name in rx.findall(line):
                    if name.lower() not in GENERIC_USERS and not name.startswith("<"):
                        found.append(f"{where}: personal home path (use <YOUR_USER>)")
    return sorted(set(found))


def test_no_operator_infrastructure_in_tracked_files():
    findings = _findings()
    assert not findings, "operator infrastructure in the public tree:\n" + "\n".join(findings)


def test_guard_catches_what_it_guards(tmp_path):
    """The rules fire on synthetic samples (built at run time, so nothing leaks here)."""
    mac = ":".join(["5a"] * 6)
    assert _MAC.findall(f"cam {mac} up") == [mac] and not _DOC_MAC.match(mac)
    assert _DOC_MAC.match("00:00:5e:00:53:01")
    assert _HOME.findall("/home/" + "zed" + "/x") == ["zed"]
    assert _WIN_HOME.findall("C:\\Users\\" + "zed" + "\\x") == ["zed"]
    assert len(OPERATOR_NAME_DIGESTS) == 10
