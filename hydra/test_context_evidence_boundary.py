"""Synthetic evidence readers must remain inside their selected directory."""
from pathlib import Path
import json
import sys

import pytest

from hydra.context_engine import assemble_context, ContextEngineError


@pytest.mark.parametrize('relative,content', [
    ('probe/results.tsv', 'now\tslice\tcase\tFAIL\tSYNTHETIC_EVIDENCE_BOUNDARY\n'),
    ('ops/probe/commands.jsonl', json.dumps({'id':'synthetic','outcome':'error','returncode':1,'stderr':'SYNTHETIC_EVIDENCE_BOUNDARY'})),
    ('ops/probe/rot_signals.json', json.dumps([{'id':'synthetic','matched':True,'match':'SYNTHETIC_EVIDENCE_BOUNDARY'}])),
])
def test_context_evidence_does_not_open_external_file(tmp_path, relative, content):
    project=tmp_path/'project';project.mkdir()
    memory=tmp_path/'memory';memory.mkdir()
    target=tmp_path/'external';target.write_text(content)
    link=project/'evidence'/relative;link.parent.mkdir(parents=True);link.symlink_to(target)
    opened=[]
    def audit(event,args):
        if event=='open' and isinstance(args[0],(str,bytes)) and Path(args[0]).resolve()==target:
            opened.append(True)
    sys.addaudithook(audit)
    result=None
    try:
        result=assemble_context(repo_root=project,memory_root=memory,max_lessons=0,max_promotions=0)
    except (ValueError,ContextEngineError):
        pass
    assert not opened
    assert 'SYNTHETIC_EVIDENCE_BOUNDARY' not in str(result)
