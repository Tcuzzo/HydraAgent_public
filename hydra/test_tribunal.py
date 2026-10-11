"""Real packet/routing/verdict behavior with only the paid transport replaced."""
import json
from types import SimpleNamespace

import pytest

from hydra.llm import ChatResponse, LlmError, OllamaClient
from hydra.tribunal import run_tribunal


@pytest.fixture
def review(tmp_path, monkeypatch):
    (tmp_path / 'author-named-file.py').write_text('answer = 41\n', encoding='utf-8')
    for name in ('judgeone', 'judgetwo', 'backup'):
        monkeypatch.setenv(name.upper() + '_ENDPOINT', f'https://{name}.invalid')
    monkeypatch.setenv('AUTHOR_PROVIDER_SENTINEL_ENDPOINT', 'https://author.invalid')
    plan = {'goal': 'Answer must be 42.', 'artifacts': ['author-named-file.py'],
            'authors': [{'provider': 'AUTHOR_PROVIDER_SENTINEL', 'model': 'AUTHOR_MODEL_SENTINEL', 'family': 'WriterFamily'}],
            'judges': [{'provider': 'judgeone', 'model': 'j1', 'family': 'JudgeFamily1'},
                       {'provider': 'judgetwo', 'model': 'j2', 'family': 'JudgeFamily2'}]}
    return tmp_path, plan


def reply(model, verdict='pass', findings=None):
    return ChatResponse(json.dumps({'verdict': verdict, 'summary': 'Evidence reviewed', 'findings': findings or []}), model, 'stop', 1, 1, {})


def test_judges_receive_fresh_blind_evidence_and_canon_without_author_metadata(review, monkeypatch):
    root, plan = review
    calls = []
    def chat(self, messages, **kwargs):
        calls.append([m.to_dict() for m in messages])
        return reply(kwargs['model'])
    monkeypatch.setattr(OllamaClient, 'chat', chat)
    result = run_tribunal(plan, root=root, output_root=root / 'runs')
    assert result['status'] == 'completed' and result['verdict'] == 'pass'
    assert len(calls) == 2 and calls[0] == calls[1]
    sent = json.dumps(calls)
    assert 'AUTHOR_' not in sent and 'WriterFamily' not in sent and 'author-named-file.py' not in sent
    assert '## Canon' in sent and '## Essence' in sent and 'answer = 41' in sent
    assert [r['actual_route']['model'] for r in result['judges']] == ['j1', 'j2']


def test_one_family_degrades_without_calls(review, monkeypatch):
    root, plan = review
    plan['judges'][1]['family'] = plan['judges'][0]['family'].lower()
    monkeypatch.setattr(OllamaClient, 'chat', lambda *a, **kw: pytest.fail('must not call insufficiently independent panel'))
    result = run_tribunal(plan, root=root, output_root=root / 'runs')
    assert result['status'] == 'degraded' and not result['verified'] and result['judges'] == []


def test_same_model_cannot_claim_independence_by_changing_family_label(review, monkeypatch):
    root, plan = review
    plan['judges'][1].update(provider='judgeone', model='j1')
    monkeypatch.setattr(OllamaClient, 'chat', lambda *a, **kw: pytest.fail('same route must not be called twice as an independent panel'))
    result = run_tribunal(plan, root=root, output_root=root / 'runs')
    assert result['reason'] == 'insufficient_independence' and not result['review_verified']


def test_author_endpoint_alias_cannot_judge_itself_with_new_family_label(review, monkeypatch):
    root, plan = review
    monkeypatch.setenv('JUDGEONE_ENDPOINT', 'https://author.invalid/v1/')
    plan['judges'][0]['model'] = plan['authors'][0]['model']
    monkeypatch.setattr(OllamaClient, 'chat', lambda *a, **kw: pytest.fail('author identity must be rejected before judging'))
    result = run_tribunal(plan, root=root, output_root=root / 'runs')
    assert result['reason'] == 'insufficient_independence' and not result['review_verified']


def test_author_alias_reached_through_fallback_cannot_certify(review, monkeypatch):
    root, plan = review
    monkeypatch.setenv('BACKUP_ENDPOINT', 'https://author.invalid/v1')
    plan['judges'][0]['fallbacks'] = [{'provider': 'backup', 'model': plan['authors'][0]['model'], 'family': 'FalseNewFamily'}]
    def chat(self, messages, **kwargs):
        if kwargs['model'] == 'j1':
            raise LlmError('No credits remaining', kind='budget')
        return reply(kwargs['model'])
    monkeypatch.setattr(OllamaClient, 'chat', chat)
    result = run_tribunal(plan, root=root, output_root=root / 'runs')
    assert result['reason'] == 'insufficient_independence' and not result['review_verified']


@pytest.mark.parametrize('failure', ['unavailable', 'invented_evidence', 'malformed', 'abstain'])
def test_failed_or_unsupported_judgement_never_certifies(review, monkeypatch, failure):
    root, plan = review
    def chat(self, messages, **kwargs):
        if kwargs['model'] == 'j2':
            return reply('j2')
        if failure == 'unavailable':
            raise LlmError('No credits remaining', kind='budget')
        if failure == 'invented_evidence':
            return reply('j1', 'fail', [{'severity': 'high', 'artifact': 'artifact-1', 'evidence': 'not in source', 'explanation': 'bad'}])
        if failure == 'malformed':
            return ChatResponse('all good', 'j1', 'stop', 1, 1, {})
        return reply('j1', 'abstain')
    monkeypatch.setattr(OllamaClient, 'chat', chat)
    result = run_tribunal(plan, root=root, output_root=root / 'runs')
    assert result['status'] == 'degraded' and result['verdict'] == 'inconclusive' and not result['verified']


def test_evidence_backed_adversarial_failure_wins(review, monkeypatch):
    root, plan = review
    def chat(self, messages, **kwargs):
        if kwargs['model'] == 'j1':
            return reply('j1', 'fail', [{'severity': 'high', 'artifact': 'artifact-1', 'evidence': 'answer = 41', 'explanation': 'Goal requires42'}])
        return reply('j2')
    monkeypatch.setattr(OllamaClient, 'chat', chat)
    result = run_tribunal(plan, root=root, output_root=root / 'runs')
    assert result['status'] == 'completed' and result['verdict'] == 'fail' and not result['verified']


def test_actual_fallback_family_collision_degrades(review, monkeypatch):
    root, plan = review
    plan['judges'][0]['fallbacks'] = [{'provider': 'backup', 'model': 'backup', 'family': 'JudgeFamily2'}]
    def chat(self, messages, **kwargs):
        if kwargs['model'] == 'j1':
            raise LlmError('No credits remaining', kind='budget')
        return reply(kwargs['model'])
    monkeypatch.setattr(OllamaClient, 'chat', chat)
    result = run_tribunal(plan, root=root, output_root=root / 'runs')
    assert result['reason'] == 'insufficient_independence' and not result['verified']
    assert result['judges'][0]['actual_route']['model'] == 'backup'


def test_packet_cannot_escape_workspace_or_exceed_limits(review):
    root, plan = review
    plan['artifacts'] = ['../outside.py']
    with pytest.raises(ValueError, match='inside the workspace'):
        run_tribunal(plan, root=root, output_root=root / 'runs')
    (root / 'large.py').write_bytes(b'x' * 65537)
    plan['artifacts'] = ['large.py']
    with pytest.raises(ValueError, match='64 KiB'):
        run_tribunal(plan, root=root, output_root=root / 'runs')


def test_team_reports_actual_family_collision_without_blocking_normal_work():
    from hydra.teams import team_independence, validate_plan
    plan = {'goal': 'work', 'provider': 'p', 'model': 'm', 'family': 'A', 'require_independent_review': True,
            'tasks': [{'id': 'build', 'specialist': 'software_engineer'},
                      {'id': 'review', 'specialist': 'code_reviewer', 'depends_on': ['build']}]}
    validate_plan(plan)
    result = team_independence(plan, [{'id': 'build', 'ok': True, 'family': 'A'}, {'id': 'review', 'ok': True, 'family': 'a'}])
    assert result['status'] == 'degraded'
