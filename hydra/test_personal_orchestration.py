"""Contracts for public profiles, real worker dispatch, and connector boundaries."""
import json
import sys
from pathlib import Path

import pytest


def test_profiles_embed_canon_and_essence_and_isolate_private_overlay(tmp_path):
    from hydra.specialists import catalog, system_prompt
    assert {p['id'] for p in catalog()} >= {'vision', 'design_taste', 'kaizen', 'lean_six_sigma', 'software_engineer', 'code_reviewer', 'test_engineer'}
    public = system_prompt('kaizen')
    assert 'PDCA' in public and 'Canon' in public and 'Essence' in public
    (tmp_path / 'kaizen.json').write_text(json.dumps({'canon': 'PRIVATE LOCAL KNOWLEDGE', 'essence': 'private working style'}))
    private = system_prompt('kaizen', private_root=tmp_path)
    assert 'PRIVATE LOCAL KNOWLEDGE' in private
    assert 'PRIVATE LOCAL KNOWLEDGE' not in system_prompt('kaizen')


def test_profile_export_detects_private_data_without_echo(tmp_path):
    from hydra.public_profiles import export_profiles, PublicProfileError
    source = tmp_path / 'source'
    source.mkdir()
    payload = {'id': 'kaizen', 'edition': 'generic-public', 'canon': 'Contact confidential.person@company.test', 'essence': 'PDCA', 'license': 'MIT', 'credits': ['Cuzzo and BACKS AIOS']}
    (source / 'kaizen.json').write_text(json.dumps(payload))
    destination = tmp_path / 'public'
    with pytest.raises(PublicProfileError) as err:
        export_profiles(source, destination, ['kaizen'])
    assert 'confidential.person' not in str(err.value)
    assert not destination.exists()
    payload['canon'] = 'Use PDCA and record the measured result.'
    (source / 'kaizen.json').write_text(json.dumps(payload))
    result = export_profiles(source, destination, ['kaizen'])
    assert result['profiles'][0]['id'] == 'kaizen'
    assert (destination / 'kaizen.json').is_file()
    assert str(source) not in (destination / 'manifest.json').read_text()


@pytest.mark.parametrize('role', ['../private', '/secret', 'C:\\secret', 'unknown'])
def test_invalid_profile_paths_rejected(role):
    from hydra.specialists import system_prompt
    with pytest.raises(ValueError):
        system_prompt(role)


def test_team_rejects_cycles_before_starting(tmp_path):
    from hydra.teams import validate_plan
    plan = {'goal': 'review', 'tasks': [
        {'id': 'a', 'specialist': 'kaizen', 'depends_on': ['b']},
        {'id': 'b', 'specialist': 'lean_six_sigma', 'depends_on': ['a']},
    ]}
    with pytest.raises(ValueError, match='cycle'):
        validate_plan(plan)


def test_team_rejects_parallel_writers():
    from hydra.teams import validate_plan
    with pytest.raises(ValueError, match='writer'):
        validate_plan({'goal': 'build', 'tasks': [
            {'id': 'a', 'specialist': 'software_engineer'},
            {'id': 'b', 'specialist': 'software_engineer'},
        ]})


def test_mcp_missing_endpoint_env_is_not_silently_ignored(tmp_path):
    from hydra.mcp_bridge import load_servers
    path = tmp_path / 'mcp.json'
    path.write_text(json.dumps({'servers': {'example': {'url_env': 'HYDRA_TEST_MISSING_ENDPOINT'}}}))
    with pytest.raises(ValueError, match='environment'):
        load_servers(path)


def test_mcp_unknown_effects_are_not_readonly():
    from hydra.mcp_bridge import is_read_only
    assert not is_read_only({'read_only_tools': []}, 'delete_record')
    assert is_read_only({'read_only_tools': ['lookup']}, 'lookup')
