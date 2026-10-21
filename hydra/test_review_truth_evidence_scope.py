"""Truth context cannot follow evidence links beyond its workspace."""
import json

import pytest

from hydra.memory_kernel import assemble_truth_context


KINDS = [('missions', 'mission_loop.json', 'mission_evidence'),
         ('source-reengineering', 'receipts.jsonl', 'source_receipt')]


def write_evidence(root, directory, filename):
    path = root/'evidence'/directory/'external'/filename
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = ({'mission': {'operator_prompt': 'Borealis private workspace marker'},
                'summary': {'verdict': 'GREEN'}} if directory == 'missions' else
               {'system_id': 'external', 'learned_pattern': 'Borealis private workspace marker'})
    path.write_text(json.dumps(payload) + '\n', encoding='utf-8')
    return path


@pytest.mark.parametrize('directory,filename,kind', KINDS)
@pytest.mark.parametrize('link_at', ['evidence', 'run', 'file'])
def test_linked_evidence_cannot_escape_selected_workspace(tmp_path, directory, filename, kind, link_at):
    selected = tmp_path/'selected'
    selected.mkdir()
    outside = tmp_path/'outside'
    source = write_evidence(outside, directory, filename)
    if link_at == 'evidence':
        target, source = selected/'evidence', outside/'evidence'
    elif link_at == 'run':
        target, source = selected/'evidence'/directory/'external', source.parent
    else:
        target = selected/'evidence'/directory/'external'/filename
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        target.symlink_to(source, target_is_directory=source.is_dir())
    except OSError as exc:
        pytest.skip(f'host cannot create test symlink: {exc}')
    result = assemble_truth_context('Borealis', repo_root=selected,
                                    memory_root=tmp_path/'memory', budget_chars=4000)
    assert 'private workspace marker' not in result['text'], result
    assert not any(row['kind'] == kind for row in result['records'])


@pytest.mark.parametrize('directory,filename,kind', KINDS)
def test_regular_workspace_evidence_is_still_recalled(tmp_path, directory, filename, kind):
    root = tmp_path/'selected'
    write_evidence(root, directory, filename)
    result = assemble_truth_context('Borealis', repo_root=root,
                                    memory_root=tmp_path/'memory', budget_chars=4000)
    assert any(row['kind'] == kind for row in result['records']), result
    assert 'private workspace marker' in result['text']
