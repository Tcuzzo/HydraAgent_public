"""Supplemental independent pass-2 source readers; synthetic local inputs only.

Run only inside the coordinator's sealed runner. No network/model/provider calls.
Negative cases assert that automatic readers do not open or emit external data.
Positive controls preserve ordinary in-root and explicitly selected root behavior.
"""
from pathlib import Path
import importlib.util
import json
import shutil
import sys
import uuid
import pytest

import hydra.skill_spine as original_spine
from hydra.prompt_builder import compose_operator_prompt
from hydra.worker_memory import build_worker_memory_context

CANARY = 'SYNTHETIC_SIBLING_INTAKE_CANARY_608a3c'


def copied_spine(root):
    source=Path(original_spine.__file__)
    target=root/'hydra'/'skill_spine.py'
    target.parent.mkdir(parents=True)
    target.write_bytes(source.read_bytes())
    assert target.read_bytes()==source.read_bytes()
    name='_static_review_spine_'+uuid.uuid4().hex
    spec=importlib.util.spec_from_file_location(name,target)
    module=importlib.util.module_from_spec(spec)
    sys.modules[name]=module
    spec.loader.exec_module(module)
    return module


def card_file(path):
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text('\n'.join([
        '- id: synthetic-review-card',
        f'  name: {CANARY}',
        '  source: synthetic-local-fixture',
        "  intent_patterns: ['probe']",
        "  mission_phases: ['inspect']",
        "  evidence_required: ['synthetic']",
        "  evals: ['synthetic-eval']",
        "  memory_links: ['synthetic-memory']",
        '',
    ]))


def observe_open(target):
    observed=[]
    resolved=target.resolve()
    def audit(event,args):
        if event!='open' or not isinstance(args[0],(str,bytes)):
            return
        try:
            path=Path(args[0]).resolve()
        except (OSError,RuntimeError,TypeError):
            return
        if path==resolved:
            observed.append('opened-synthetic-external-file')
    sys.addaudithook(audit)
    return observed


def assert_automatic_rejects(target,reader):
    opened=observe_open(target)
    result=None
    try:
        result=reader()
    except Exception:
        assert not opened, 'automatic reader opened external synthetic source before rejecting'
        return
    assert not opened, 'automatic reader opened external synthetic source'
    assert CANARY not in json.dumps(result,default=str), 'external canary reached automatic context'


@pytest.mark.parametrize('link_kind',['file','catalog_root','parent'])
def test_default_capability_routing_rejects_external_source(tmp_path,monkeypatch,link_kind):
    monkeypatch.delenv('HYDRA_SKILLS_ROOT',raising=False)
    package=tmp_path/'synthetic-package'
    spine=copied_spine(package)
    external=tmp_path/'external-cards'
    target=external/'capabilities'/'probe.yaml'
    card_file(target)
    selected=package/'.hydraAgent'/'capabilities'
    if link_kind=='file':
        selected.mkdir(parents=True)
        (selected/'probe.yaml').symlink_to(target)
    elif link_kind=='catalog_root':
        selected.parent.mkdir(parents=True)
        selected.symlink_to(target.parent,target_is_directory=True)
    else:
        selected.parent.symlink_to(external,target_is_directory=True)
    assert_automatic_rejects(target,lambda:spine.build_routed_skill_context('probe'))


def test_default_capability_routing_allows_ordinary_package_card(tmp_path,monkeypatch):
    monkeypatch.delenv('HYDRA_SKILLS_ROOT',raising=False)
    package=tmp_path/'synthetic-package'
    spine=copied_spine(package)
    card_file(package/'.hydraAgent'/'capabilities'/'probe.yaml')
    result=spine.build_routed_skill_context('probe')
    assert CANARY in result


def test_explicit_capability_root_remains_usable(tmp_path):
    spine=copied_spine(tmp_path/'synthetic-package')
    external=tmp_path/'operator-selected-cards'
    card_file(external/'probe.yaml')
    result=spine.route_capability_cards('probe',root=external)
    assert CANARY in json.dumps([card.to_dict() for card in result])


@pytest.mark.parametrize('external_link',[True,False])
def test_automatic_status_context_respects_workspace(tmp_path,external_link):
    project=tmp_path/'selected-project';project.mkdir()
    memory=tmp_path/'selected-empty-memory';memory.mkdir()
    content='## Promotions\n- `2026-11-26` '+CANARY+'\n'
    if external_link:
        target=tmp_path/'external-status.md';target.write_text(content)
        (project/'STATUS.md').symlink_to(target)
    else:
        target=project/'STATUS.md';target.write_text(content)
    def reader():
        return compose_operator_prompt('synthetic base',repo_root=project,memory_root=memory,
            max_lessons=0,max_clusters=0,max_promotions=8)['prompt']
    if external_link:
        assert_automatic_rejects(target,reader)
    else:
        assert CANARY in reader()


@pytest.mark.parametrize('external_link',[True,False])
def test_automatic_worker_wiki_context_respects_workspace(tmp_path,external_link):
    project=tmp_path/'selected-project';project.mkdir()
    memory=tmp_path/'selected-empty-memory';memory.mkdir()
    wiki=project/'.hydraAgent'/'wiki';wiki.parent.mkdir()
    if external_link:
        outside=tmp_path/'external-wiki';outside.mkdir()
        target=outside/'probe.md';target.write_text('# synthetic\n'+CANARY+'\n')
        wiki.symlink_to(outside,target_is_directory=True)
    else:
        wiki.mkdir();target=wiki/'probe.md';target.write_text('# synthetic\n'+CANARY+'\n')
    def reader():
        return build_worker_memory_context(query=CANARY,repo_root=project,memory_root=memory)
    if external_link:
        assert_automatic_rejects(target,reader)
    else:
        assert CANARY in json.dumps(reader())


@pytest.mark.parametrize('external_link',[True,False])
def test_automatic_worker_convention_respects_workspace(tmp_path,external_link):
    project=tmp_path/'selected-project';project.mkdir()
    memory=tmp_path/'selected-empty-memory';memory.mkdir()
    dest=project/'.hydraAgent'/'PRINCIPLES.md';dest.parent.mkdir()
    if external_link:
        target=tmp_path/'external-conventions.md';target.write_text(CANARY+'\n')
        dest.symlink_to(target)
    else:
        target=dest;target.write_text(CANARY+'\n')
    def reader():
        return build_worker_memory_context(query='probe',repo_root=project,memory_root=memory)
    if external_link:
        assert_automatic_rejects(target,reader)
    else:
        assert CANARY in json.dumps(reader())
