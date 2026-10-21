"""Runtime evidence checks shared by team plan validation and worker completion.

These checks establish specific observable conditions, not general task quality.
Only selected workspace files are read; model prose is never proof.
"""
from __future__ import annotations

import hashlib
from pathlib import Path, PureWindowsPath
import re

from hydra.specialists import OBSERVERS, canonical_path, reject_links

MAX_ARTIFACT_BYTES = 16 * 1024 * 1024
NON_OBSERVATION_TOOLS = frozenset({
    'needle_select', 'laya_decide', 'kev_decide', 'todo', 'skill_list', 'skill_show',
    'skill_route', 'skill_search', 'memory_remember', 'memory_recall', 'mcp_tools', 'mcp_servers',
})


def validate_acceptance(value: object) -> dict:
    if not isinstance(value, dict) or set(value) - {'require_observation', 'artifacts'}:
        raise ValueError('acceptance requires an observation flag and/or workspace artifacts')
    if type(value.get('require_observation', False)) is not bool:
        raise ValueError('require_observation must be a boolean')
    artifacts = value.get('artifacts', [])
    if not isinstance(artifacts, list) or len(artifacts) > 16:
        raise ValueError('acceptance permits at most sixteen artifacts')
    seen = set()
    for artifact in artifacts:
        _validate_artifact(artifact)
        path = artifact['path']
        if path.casefold() in seen:
            raise ValueError('acceptance artifacts must be distinct')
        seen.add(path.casefold())
    return value


def _validate_artifact(value: object) -> None:
    if not isinstance(value, dict) or set(value) - {'path', 'sha256', 'must_change'}:
        raise ValueError('artifact requires path and optional sha256/must_change')
    path = value.get('path')
    if not isinstance(path, str) or not path.strip() or len(path) > 512 or '\x00' in path:
        raise ValueError('artifact path must be bounded nonempty text')
    posix, windows = Path(path), PureWindowsPath(path)
    if posix.is_absolute() or windows.root or windows.drive or '..' in posix.parts or '..' in windows.parts:
        raise ValueError('artifact must be a relative path inside the workspace')
    if 'sha256' in value and (not isinstance(value['sha256'], str) or not re.fullmatch(r'[a-f0-9]{64}', value['sha256'])):
        raise ValueError('artifact sha256 must be 64 lowercase hexadecimal digits')
    if type(value.get('must_change', False)) is not bool:
        raise ValueError('artifact must_change must be a boolean')


def _artifact_state(root: Path, relative: str) -> dict:
    path = root / relative
    reject_links(path)
    resolved = canonical_path(path)
    if not resolved.is_relative_to(root):
        raise ValueError('artifact resolves outside the workspace')
    if not path.exists():
        return {'path': relative, 'exists': False, 'sha256': None, 'bytes': 0}
    if not path.is_file():
        raise ValueError('artifact must be a regular file')
    digest, size = hashlib.sha256(), 0
    with path.open('rb') as stream:
        while chunk := stream.read(65536):
            size += len(chunk)
            if size > MAX_ARTIFACT_BYTES:
                raise ValueError('acceptance artifact exceeds 16 MiB')
            digest.update(chunk)
    return {'path': relative, 'exists': True, 'sha256': digest.hexdigest(), 'bytes': size}


def snapshot_artifacts(root: Path, contract: dict) -> dict[str, dict]:
    validate_acceptance(contract)
    return {row['path']: _artifact_state(root, row['path']) for row in contract.get('artifacts', [])}


def _check_artifacts(root: Path, contract: dict, before: dict[str, dict]) -> tuple[list[dict], list[str]]:
    rows, reasons = [], []
    for wanted in contract.get('artifacts', []):
        try:
            state = _artifact_state(root, wanted['path'])
        except (ValueError, OSError) as exc:
            reasons.append(f"Artifact {wanted['path']}: {exc}")
            continue
        state['changed'] = state['sha256'] != before[wanted['path']]['sha256']
        rows.append(state)
        if not state['exists']:
            reasons.append(f"Required artifact is missing: {wanted['path']}")
        if wanted.get('sha256') and state['sha256'] != wanted['sha256']:
            reasons.append(f"Required artifact hash differs: {wanted['path']}")
        if wanted.get('must_change') and not state['changed']:
            reasons.append(f"Required artifact did not change: {wanted['path']}")
    return rows, reasons


def check_acceptance(*, role: str, root: Path, contract: dict, before: dict[str, dict],
                     evidence: list[dict], native: bool = False) -> dict:
    artifacts, reasons = _check_artifacts(root, contract, before)
    observed = any(not row['error'] and row['tool'] not in NON_OBSERVATION_TOOLS for row in evidence)
    artifact_proof = bool(artifacts) and not reasons
    required = role in OBSERVERS or contract.get('require_observation', False)
    if required and not (observed or artifact_proof):
        detail = ('Native client tool events are not visible to Hydra; declare acceptance artifacts '
                  'with expected hashes or must_change when appropriate.' if native else
                  'Use a tool to inspect or act on the task, then report its observed result.')
        reasons.append('Required task observation is missing. ' + detail)
    return {'status': 'failed' if reasons else 'passed', 'reasons': reasons,
            'require_observation': required, 'tool_observation': observed,
            'artifacts': artifacts,
            'scope': 'Observed tool outcomes and declared file conditions only; no general quality certification.'}
