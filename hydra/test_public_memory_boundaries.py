"""Synthetic source-boundary checks; no models or external network calls."""
from pathlib import Path
import json
import sys

import pytest

import hydra.local_memory as local
from hydra.memory_kernel import build_memory_index
from hydra.semantic_recall import resolve_corpus_chunks

CANARY = 'SYNTHETIC_MEMORY_BOUNDARY_9ed873'


def reject_open(target, reader):
    seen = []
    resolved = target.resolve()

    def audit(event, args):
        if event == 'open' and isinstance(args[0], (str, bytes)):
            if Path(args[0]).resolve() == resolved:
                seen.append(True)

    sys.addaudithook(audit)
    result = None
    try:
        result = reader()
    except (ValueError, OSError):
        pass
    assert not seen, 'external synthetic file was opened'
    assert CANARY not in str(result)


@pytest.mark.parametrize('consumer', ['local', 'semantic'])
@pytest.mark.parametrize('opt_in', [False, True])
def test_claude_memory_requires_explicit_opt_in(tmp_path, monkeypatch, consumer, opt_in):
    home = tmp_path / 'home'
    memory = home / '.hydra-memory'
    memory.mkdir(parents=True)
    workspace = tmp_path / 'project'
    workspace.mkdir()
    monkeypatch.setattr(local.Path, 'home', lambda: home)
    monkeypatch.setattr(local, 'DEFAULT_MEMORY_ROOT', memory)
    monkeypatch.delenv('HYDRA_IMPORT_CLAUDE_MEMORY', raising=False)
    slug = str(workspace.resolve()).replace('/', '-')
    target = home / '.claude' / 'projects' / slug / 'memory' / 'MEMORY.md'
    target.parent.mkdir(parents=True)
    target.write_text('# Memory\n' + CANARY)
    if opt_in:
        monkeypatch.setenv('HYDRA_IMPORT_CLAUDE_MEMORY', '1')

    def read():
        if consumer == 'local':
            return local.build_local_memory_context(memory, workspace_root=workspace).context
        return resolve_corpus_chunks(memory, workspace)

    if opt_in:
        assert CANARY in str(read())
    else:
        reject_open(target, read)


@pytest.mark.parametrize('consumer', ['local', 'semantic'])
@pytest.mark.parametrize('link_kind', ['file', 'parent'])
def test_explicit_memory_root_confines_descendants(tmp_path, monkeypatch, consumer, link_kind):
    monkeypatch.delenv('HYDRA_IMPORT_CLAUDE_MEMORY', raising=False)
    memory = tmp_path / 'selected-memory'
    memory.mkdir()
    target = tmp_path / 'outside' / 'MEMORY.md'
    target.parent.mkdir()
    target.write_text('# Memory\n' + CANARY)
    if link_kind == 'file':
        (memory / 'workspace').mkdir()
        (memory / 'workspace' / 'MEMORY.md').symlink_to(target)
    else:
        (memory / 'workspace').symlink_to(target.parent, target_is_directory=True)

    def read():
        if consumer == 'local':
            return local.build_local_memory_context(memory).context
        return resolve_corpus_chunks(memory, tmp_path)

    reject_open(target, read)


@pytest.mark.parametrize('consumer', ['local', 'semantic'])
def test_explicit_memory_root_remains_usable(tmp_path, consumer):
    memory = tmp_path / 'selected-memory'
    (memory / 'workspace').mkdir(parents=True)
    (memory / 'workspace' / 'MEMORY.md').write_text('# Memory\n' + CANARY)
    if consumer == 'local':
        result = local.build_local_memory_context(memory).context
    else:
        result = resolve_corpus_chunks(memory, tmp_path)
    assert CANARY in str(result)


@pytest.mark.parametrize('relative,content', [
    ('.hydraAgent/wiki/probe.md', '# Memory\n' + CANARY),
    ('evidence/missions/probe/mission_loop.json', json.dumps({'mission': {'operator_prompt': CANARY}})),
    ('evidence/source-reengineering/probe/receipts.jsonl', json.dumps({'learned_pattern': CANARY})),
])
def test_worker_index_confines_each_record_file(tmp_path, relative, content):
    project = tmp_path / 'project'
    project.mkdir()
    memory = tmp_path / 'memory'
    memory.mkdir()
    target = tmp_path / 'outside.txt'
    target.write_text(content)
    link = project / relative
    link.parent.mkdir(parents=True)
    link.symlink_to(target)
    reject_open(target, lambda: build_memory_index(repo_root=project, memory_root=memory))
