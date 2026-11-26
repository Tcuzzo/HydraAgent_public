"""Frozen independent source-intake regression candidates; only synthetic fixtures.

Default package loaders are tested from byte-identical module copies in a
synthetic package layout. This relocates __file__ without patching readers.
"""
from pathlib import Path
import importlib.util
import json
import shutil
import sys
import uuid

import pytest

import hydra.declarative_runtime as runtime
import hydra.dev_mode_seam as dev_mode

CANARY = 'SYNTHETIC_SOURCE_INTAKE_CANARY_b521cf'


def module_copy(original, root):
    path = root / 'hydra' / Path(original.__file__).name
    path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(original.__file__, path)
    assert path.read_bytes() == Path(original.__file__).read_bytes()
    name = '_review_' + uuid.uuid4().hex
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def contracts(base):
    content = {
        'tools/registry.yaml': 'tools: []\n',
        'skills/index.yaml': 'skills: []\n',
        'ux/response-contracts.yaml': 'voice: synthetic\n',
        'policies/danger-gates.yaml': 'gates: []\n',
        'policies/trust-tiers.yaml': 'tiers: {}\n',
        'policies/backs-invariants.yaml': 'name: synthetic\ninvariants: []\n',
        'policies/sniper-testing.yaml': 'rules: []\n',
        'policies/anti-mock-theater.yaml': 'rules: []\n',
        'playbooks/dev-mode-elite-build.yaml': 'name: synthetic\nstages: []\n',
    }
    for relative, text in content.items():
        path = base / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)


def observe_external_opens(target):
    seen = []
    resolved = target.resolve()
    def hook(event, args):
        if event != 'open' or not isinstance(args[0], (str, bytes)):
            return
        try:
            path = Path(args[0]).resolve()
        except (OSError, RuntimeError, TypeError):
            return
        if path == resolved:
            seen.append('opened-synthetic-external-file')
    sys.addaudithook(hook)
    return seen


@pytest.mark.parametrize('layout', ['source', 'installed'])
def test_default_declarative_catalog_root_cannot_escape_package(tmp_path, layout):
    package = tmp_path / 'synthetic-package'
    copied = module_copy(runtime, package)
    outside = tmp_path / 'external-catalog'
    contracts(outside)
    target = outside / 'ux/response-contracts.yaml'
    target.write_text(f'voice: {CANARY}\n')
    catalog = package / ('.hydraAgent' if layout == 'source' else 'hydra/runtime_data/catalog')
    catalog.parent.mkdir(parents=True, exist_ok=True)
    catalog.symlink_to(outside, target_is_directory=True)
    project = tmp_path / 'selected-project'
    project.mkdir()
    opened = observe_external_opens(target)
    try:
        result = copied.load_runtime_catalog(project)
    except Exception:
        assert opened == [], 'Reader opened escaped catalog before raising'
        return
    assert opened == [], 'Default declarative loader opened file through escaped catalog root'
    assert CANARY not in json.dumps(result.ux)


@pytest.mark.parametrize('layout', ['source', 'installed'])
@pytest.mark.parametrize('link_kind', ['file', 'parent', 'root'])
def test_default_dev_mode_catalog_cannot_escape_package(tmp_path, layout, link_kind):
    package = tmp_path / 'synthetic-package'
    copied = module_copy(dev_mode, package)
    base = package / ('.hydraAgent' if layout == 'source' else 'hydra/runtime_data/catalog')
    external = tmp_path / 'external-catalog'
    contracts(base)
    contracts(external)
    target = external / 'policies/backs-invariants.yaml'
    target.write_text(f'name: {CANARY}\ninvariants: []\n')
    if link_kind == 'file':
        selected = base / 'policies/backs-invariants.yaml'
        selected.unlink()
        selected.symlink_to(target)
    elif link_kind == 'parent':
        shutil.rmtree(base / 'policies')
        (base / 'policies').symlink_to(external / 'policies', target_is_directory=True)
    else:
        shutil.rmtree(base)
        base.symlink_to(external, target_is_directory=True)
    opened = observe_external_opens(target)
    try:
        result = copied.build_backs_dev_mode_doctrine()
    except Exception:
        assert opened == [], 'Reader opened escaped policy before raising'
        return
    assert opened == [], 'Default system doctrine opened a policy outside package'
    assert CANARY not in result


@pytest.mark.parametrize('link_kind', ['file', 'parent', 'root'])
def test_declarative_episode_jsonl_cannot_escape_selected_project(tmp_path, link_kind):
    project = tmp_path / 'selected-project'
    base = project / '.hydraAgent'
    (base / 'memory').mkdir(parents=True)
    external = tmp_path / 'external-state'
    (external / 'memory').mkdir(parents=True)
    target = external / 'memory/episodic.jsonl'
    target.write_text(json.dumps({'note': CANARY}) + '\n')
    if link_kind == 'file':
        (base / 'memory/episodic.jsonl').symlink_to(target)
    elif link_kind == 'parent':
        (base / 'memory').rmdir()
        (base / 'memory').symlink_to(external / 'memory', target_is_directory=True)
    else:
        (base / 'memory').rmdir()
        base.rmdir()
        base.symlink_to(external, target_is_directory=True)
    opened = observe_external_opens(target)
    try:
        result = runtime._recent_episodes(project)
    except Exception:
        assert opened == [], 'Reader opened escaped JSONL before raising'
        return
    assert opened == [], 'Runtime brief episode loader opened an external JSONL file'
    assert CANARY not in json.dumps(result)


def test_explicit_dev_mode_catalog_remains_supported(tmp_path):
    catalog = tmp_path / 'operator-selected'
    contracts(catalog)
    (catalog / 'policies/backs-invariants.yaml').write_text(f'name: {CANARY}\ninvariants: []\n')
    assert CANARY in dev_mode.build_backs_dev_mode_doctrine(catalog)


def test_internal_catalog_alias_remains_supported(tmp_path):
    project = tmp_path / 'selected-project'
    contracts(project / 'local-catalog')
    (project / 'local-catalog/ux/response-contracts.yaml').write_text(f'voice: {CANARY}\n')
    (project / '.hydraAgent').symlink_to('local-catalog', target_is_directory=True)
    result = runtime.load_runtime_catalog(project)
    assert CANARY in json.dumps(result.ux)


@pytest.mark.parametrize('layout', ['source', 'installed'])
@pytest.mark.parametrize('loader', ['declarative', 'dev-mode'])
def test_plain_default_catalog_layout_is_valid(tmp_path, layout, loader):
    package = tmp_path / 'synthetic-package'
    original = runtime if loader == 'declarative' else dev_mode
    copied = module_copy(original, package)
    base = package / ('.hydraAgent' if layout == 'source' else 'hydra/runtime_data/catalog')
    contracts(base)
    if loader == 'declarative':
        project = tmp_path / 'selected-project'
        project.mkdir()
        assert copied.load_runtime_catalog(project).ux['voice'] == 'synthetic'
    else:
        assert all(copied.load_dev_mode_contracts()['loaded'].values())


def test_default_model_routing_yaml_does_not_open_external_link(tmp_path):
    import hydra.model_routing as routing
    package = tmp_path / 'synthetic-package'
    copied = module_copy(routing, package)
    outside = tmp_path / 'external-routing.yaml'
    outside.write_bytes(Path(routing.DEFAULT_ROUTING_PATH).read_bytes())
    (package / 'hydra/model_routing.yaml').symlink_to(outside)
    opened = observe_external_opens(outside)
    try:
        copied.load_routing(refresh=True)
    except Exception:
        assert opened == [], 'Routing loader opened escaped YAML before raising'
        return
    assert opened == [], 'Default model routing loader opened YAML outside package'


def test_explicit_model_routing_yaml_remains_supported(tmp_path):
    import hydra.model_routing as routing
    outside = tmp_path / 'operator-selected-routing.yaml'
    outside.write_bytes(Path(routing.DEFAULT_ROUTING_PATH).read_bytes())
    assert routing.load_routing(outside, refresh=True).source_path == str(outside)
