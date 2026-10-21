"""Explicit memory_recall uses the same selected source boundary as retrieval."""
import pytest

from hydra.cli.tool_binding import bind_tools


@pytest.mark.parametrize('linked_parent', [False, True])
def test_explicit_recall_cannot_follow_another_projects_memory(tmp_path, linked_parent):
    selected = tmp_path/'selected'
    selected.mkdir()
    memory = tmp_path/'memory'
    memory.mkdir()
    outside = tmp_path/'outside-project'
    outside.mkdir()
    source = outside/'MEMORY.md'
    source.write_text('Meridian confidential external-project marker 968c\n', encoding='utf-8')
    target = memory/'workspace'
    if not linked_parent:
        target.mkdir()
        target = target/'MEMORY.md'
    try:
        target.symlink_to(outside if linked_parent else source, target_is_directory=linked_parent)
    except OSError as exc:
        pytest.skip(f'host cannot create test symlink: {exc}')
    tool = next(t for t in bind_tools(selected, approval_policy='deny', memory_root=memory,
                                     include_mcp=False) if t.name == 'memory_recall')
    result = tool.invoke(max_chars=4000)
    assert 'external-project marker 968c' not in result['context'], result
    assert str(source) not in result['context']
