"""Bounded specialist task graphs using Hydra's existing subprocess dispatcher."""
from __future__ import annotations

import json
import re
import sys
import uuid
from pathlib import Path

from hydra.orchestrate import SubagentTask, dispatch_graph
from hydra.specialists import ROLES, WRITERS, read_json, canonical_path, reject_links


def make_plan(goal: str, provider: str, model: str) -> dict:
    dependencies = {'vision': [], 'design_taste': ['vision'], 'software_engineer': ['design_taste'],
                    'test_engineer': ['software_engineer'], 'code_reviewer': ['test_engineer'],
                    'kaizen': ['code_reviewer'], 'lean_six_sigma': ['code_reviewer']}
    return {'schema': 'hydra.team.v1', 'goal': goal, 'provider': provider, 'model': model,
            'max_concurrency': 2, 'timeout_seconds': 300, 'max_iterations': 8,
            'tasks': [{'id': role, 'specialist': role, 'depends_on': dependencies[role]} for role in ROLES]}


def validate_plan(plan: dict) -> dict:
    if not isinstance(plan, dict) or not isinstance(plan.get('goal'), str) or not plan['goal'].strip() or len(plan['goal']) > 16000:
        raise ValueError('team goal must be nonempty text of at most 16000 characters')
    if set(plan) - {'schema', 'goal', 'provider', 'model', 'family', 'max_concurrency', 'max_iterations', 'timeout_seconds', 'tasks', 'require_independent_review'}:
        raise ValueError('unknown team plan fields')
    if plan.get('schema', 'hydra.team.v1') != 'hydra.team.v1' or type(plan.get('require_independent_review', False)) is not bool:
        raise ValueError('invalid team schema or review flag')
    tasks = plan.get('tasks')
    if not isinstance(tasks, list) or not 1 <= len(tasks) <= 64 or not all(isinstance(t, dict) for t in tasks):
        raise ValueError('team requires between 1 and 64 task objects')
    ids = [task.get('id') for task in tasks]
    if any(not isinstance(i, str) or not re.fullmatch(r'[a-zA-Z0-9_-]{1,64}', i) for i in ids):
        raise ValueError('task IDs must be unique safe identifiers')
    if len(ids) != len({i.casefold() for i in ids}) or any(re.fullmatch(r'(?i)(con|prn|aux|nul|com[0-9]|lpt[0-9])', i) for i in ids):
        raise ValueError('task IDs must be unique safe identifiers')
    by_id = dict(zip(ids, tasks))
    for task in tasks:
        if set(task) - {'id', 'specialist', 'depends_on', 'prompt', 'provider', 'model', 'family', 'images'}:
            raise ValueError('unknown task fields')
        if task.get('specialist') not in ROLES:
            raise ValueError('unknown specialist')
        deps = task.get('depends_on', [])
        if not isinstance(deps, list) or not all(isinstance(d, str) and d in by_id for d in deps):
            raise ValueError('task has an unknown dependency')
    ancestors = {}
    visiting = set()
    def visit(task_id):
        if task_id in visiting:
            raise ValueError('team dependency cycle')
        if task_id in ancestors:
            return ancestors[task_id]
        visiting.add(task_id)
        result = set()
        for dep in by_id[task_id].get('depends_on', []):
            result.add(dep)
            result.update(visit(dep))
        visiting.remove(task_id)
        ancestors[task_id] = result
        return result
    for task_id in ids:
        visit(task_id)
    writers = [t['id'] for t in tasks if t['specialist'] in WRITERS]
    # Writers and readers never race over a changing workspace. Multiple independent
    # readers can share a phase, but every writer must be ordered relative to all jobs.
    for writer in writers:
        for other in ids:
            if other != writer and other not in ancestors[writer] and writer not in ancestors[other]:
                raise ValueError('workspace writer must be dependency-ordered relative to every other task')
    for key, default, maximum in [('max_concurrency', 2, 16), ('max_iterations', 8, 100), ('timeout_seconds', 300, 3600)]:
        value = plan.get(key, default)
        if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= maximum:
            raise ValueError(f'{key} must be an integer between 1 and {maximum}')
    for task in tasks:
        if not isinstance(task.get('prompt', ''), str) or len(task.get('prompt', '')) > 16000:
            raise ValueError('task prompt exceeds its text limit')
        for key in ('provider', 'model', 'family'):
            value = task.get(key, plan.get(key, ''))
            if not isinstance(value, str) or len(value) > 256:
                raise ValueError(f'{key} must be bounded text')
        if 'images' in task and (not isinstance(task['images'], list) or len(task['images']) > 4 or not all(isinstance(p, str) for p in task['images'])):
            raise ValueError('images must be a list of at most four workspace paths')
    if plan.get('require_independent_review'):
        builders = [t for t in tasks if t['specialist'] == 'software_engineer']
        reviewers = [t for t in tasks if t['specialist'] == 'code_reviewer']
        if not reviewers:
            raise ValueError('independent review requires a reviewer task')
        for reviewer in reviewers:
            rf = reviewer.get('family', plan.get('family'))
            for builder in builders:
                if builder['id'] not in ancestors[reviewer['id']]:
                    raise ValueError('independent reviewer must depend on every builder')
                bf = builder.get('family', plan.get('family'))
                if not rf or not bf or rf.casefold() == bf.casefold():
                    raise ValueError('independent review requires explicit distinct configured model families')
    return plan


def run_team(plan: dict, *, root: Path, output_root: Path, approval_policy: str = 'ask', private_root: Path | None = None, env_dir: Path | None = None) -> dict:
    validate_plan(plan)
    root = canonical_path(root)
    if private_root:
        reject_links(private_root)
    private_root = canonical_path(private_root) if private_root else None
    env_dir = canonical_path(env_dir) if env_dir else None
    if any(path is not None and not path.is_dir() for path in (private_root, env_dir)):
        raise ValueError('explicit profile and environment directories must exist')
    if not root.is_dir() or approval_policy not in {'ask', 'allow', 'deny'}:
        raise ValueError('team requires an existing workspace and a valid approval policy')
    for task in plan['tasks']:
        if not task.get('provider', plan.get('provider')) or not task.get('model', plan.get('model')):
            raise ValueError('every task requires an explicitly configured provider and model')
    run_dir = canonical_path(output_root) / uuid.uuid4().hex
    run_dir.mkdir(parents=True)
    jobs = []
    for task in plan['tasks']:
        request = {'task': task, 'goal': plan['goal'], 'root': str(root),
                   'provider': task.get('provider', plan.get('provider')), 'model': task.get('model', plan.get('model')),
                   'family': task.get('family', plan.get('family', 'unspecified')),
                   'approval_policy': approval_policy, 'max_iterations': plan.get('max_iterations', 8),
                   'timeout_seconds': plan.get('timeout_seconds', 300),
                   'private_root': str(private_root) if private_root else None,
                   'env_dir': str(env_dir) if env_dir else None}
        packet = run_dir / f'{task["id"]}.request.json'
        packet.write_text(json.dumps(request, ensure_ascii=False), encoding='utf-8')
        jobs.append(SubagentTask(id=task['id'], command=[sys.executable, '-I', '-m', 'hydra.team_worker', '--request', str(packet)],
                                 cwd=str(root), timeout_seconds=plan.get('timeout_seconds', 300), depends_on=task.get('depends_on', [])))
    report = dispatch_graph(jobs, max_concurrency=plan.get('max_concurrency', 2))
    outcomes = []
    for task in plan['tasks']:
        result_path = run_dir / f'{task["id"]}.result.json'
        if result_path.is_file():
            outcomes.append(read_json(result_path, 131072))
    report.update(schema='hydra.team.v1', run_id=run_dir.name, run_directory=str(run_dir), outcomes=outcomes,
                  verified=False, verification_note='Completed execution is not a quality certification; inspect artifacts and recorded evidence.')
    (run_dir / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    return report
