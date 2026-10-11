"""Distributed hive context must not revive private deployment instructions."""
import json
from pathlib import Path
import re
import yaml


def test_public_roster_profiles_are_generic_and_consistent():
    root = Path(__file__).resolve().parents[1] / 'skills/backs-aios-skills-pub/hive-clone-boot'
    roster = yaml.safe_load((root / 'roster.yaml').read_text(encoding='utf-8'))
    profiles = json.loads((root / 'references/agents_profiles_full.json').read_text(encoding='utf-8'))
    assert roster['contract'] == 'public_hive.v1'
    assert {a['id'] for a in roster['agents']} == {a['id'] for a in profiles}
    for agent in roster['agents']:
        assert agent['id'] == 'public-' + agent['slug']
        assert agent['backs_rung_role'] in {'worker', 'reviewer'}
        body = (root / agent['soul_file']).read_text(encoding='utf-8')
        assert next(p for p in profiles if p['id'] == agent['id'])['description'] == body
    for path in root.rglob('*'):
        if path.is_file() and path.suffix in {'.md', '.json', '.yaml'}:
            body = path.read_text(encoding='utf-8')
            assert not re.search(r'[A-Za-z]:\\Users\\[^\\\s]+|/home/[^/\s]+/|machineId|[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}', body), path
            assert 'ENDORSED GREEN' not in body, path
