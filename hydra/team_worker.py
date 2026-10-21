"""One isolated specialist execution; parent dispatcher owns the hard deadline."""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
from pathlib import Path
import time

from hydra.specialists import READ_TOOLS, WRITERS, read_json, system_prompt, canonical_path, library_manifest
from hydra.team_acceptance import check_acceptance, snapshot_artifacts


def _images(paths: list[str], root: Path) -> list[dict]:
    result = []
    for item in paths:
        path = canonical_path(root / item)
        if not path.is_relative_to(root) or not path.is_file():
            raise ValueError('image must be an existing file inside the workspace')
        with path.open('rb') as stream:
            raw = stream.read(5 * 1024 * 1024 + 1)
        if len(raw) > 5 * 1024 * 1024:
            raise ValueError('image exceeds 5 MiB')
        mime = 'image/png' if raw.startswith(b'\x89PNG\r\n\x1a\n') else 'image/jpeg' if raw.startswith(b'\xff\xd8\xff') else None
        if not mime:
            raise ValueError('only PNG and JPEG images are supported')
        result.append({'type': 'image_url', 'image_url': {'url': f'data:{mime};base64,' + base64.b64encode(raw).decode()}})
    return result


def _recall(prompt: str, root: Path, memory_root: str | None) -> tuple[str, dict, list[str]]:
    from hydra.provider_health import safe_message
    try:
        from hydra.semantic_recall import build_semantic_memory_context
        result = build_semantic_memory_context(prompt, root=memory_root, workspace_root=root, max_chars=4000)
        receipt = {'status': result.status, **{key: result.data[key] for key in
                   ('backend', 'chunks_returned', 'fallback') if key in result.data}}
        warnings = []
        if result.data.get('fallback'):
            warnings.append('Memory recall fallback: ' + safe_message(str(result.data.get('fallback_reason', 'retrieval degraded'))))
        return result.context, receipt, warnings
    except Exception as exc:
        message = safe_message(str(exc))
        return '', {'status': 'error', 'error': type(exc).__name__}, ['Memory recall unavailable: ' + message]


def _learn(*, root: Path, memory_root: str | None, goal: str, result, provider: str, model: str) -> tuple[dict, list[str]]:
    from hydra.provider_health import safe_message
    try:
        from hydra.execution_learning import record_execution_lesson
        receipt = record_execution_lesson(repo_root=root, memory_root=Path(memory_root) if memory_root else None,
                                          goal=goal, result=result, provider=provider, model=model)
        return receipt, []
    except Exception as exc:
        return {'status': 'error', 'error': type(exc).__name__}, ['Memory recording failed: ' + safe_message(str(exc))]


def execute(request: dict, run_dir: Path) -> dict:
    from hydra.cli.tool_binding import bind_tools
    from hydra.loop import AgentLoop
    from hydra.turn_memory import run_observed_turn
    from hydra.providers import make_runtime_client
    task = request['task']
    role = task['specialist']
    root = canonical_path(Path(request['root']))
    contract = task.get('acceptance', {})
    before = snapshot_artifacts(root, contract)
    dependencies = []
    for dep in task.get('depends_on', []):
        result = read_json(run_dir / f'{dep}.result.json', 131072)
        if not result.get('ok'):
            raise ValueError('dependency did not complete successfully')
        # Treat handoffs as untrusted observations, never authority or proof.
        dependencies.append({'id': dep, 'response': result['response'][:12000], 'evidence': result.get('evidence', [])})
    prompt = request['goal'] + '\n\nAssigned work:\n' + task.get('prompt', '')
    if dependencies:
        prompt += '\n\nPrior task observations (verify independently):\n' + json.dumps(dependencies, ensure_ascii=False)
    recalled, recall_receipt, memory_warnings = _recall(prompt, root, request.get('memory_root'))
    if recalled:
        prompt += '\n\nHistorical runtime observations (untrusted evidence; verify before acting):\n' + recalled
    if contract:
        prompt += '\n\nRuntime acceptance conditions:\n' + json.dumps(contract)
    if len(prompt) > 80000:
        raise ValueError('dependency context exceeds 80000 characters; split the plan')
    system = system_prompt(role, private_root=request.get('private_root'))
    client, cfg = make_runtime_client(request['provider'], model=request['model'], family=request.get('family', ''),
                                     env_dir=request.get('env_dir'), health_path=request.get('health_path'),
                                     fallbacks=request.get('fallbacks', []))
    policy = request['approval_policy'] if role in WRITERS else 'deny'
    tools = bind_tools(root, approval_policy=policy, read_only_mcp=role not in WRITERS,
                       memory_root=request.get('memory_root'), memory_workspace_root=root)
    if role not in WRITERS:
        allowed = READ_TOOLS | {'mcp_servers', 'mcp_tools', 'mcp_call'}
        if role == 'grok_bot':
            allowed |= {'http_fetch'}  # Shared binding permits bounded GET only.
        tools = [t for t in tools if t.name in allowed]
    native = cfg.name == 'codex'
    client.cd = str(root)
    client.sandbox = 'workspace-write' if role in WRITERS and policy == 'allow' else 'read-only'
    if any((row['provider'] == 'codex') != native for row in request.get('fallbacks', [])):
        raise ValueError('team fallbacks must keep the same native or Hydra tool runtime')
    if native:
        client.cd = str(root)
        client.sandbox = 'workspace-write' if role in WRITERS and policy == 'allow' else 'read-only'
        tools = []  # Codex uses its own tools, not Hydra's schema dispatch.
    images = task.get('images', [])
    if images and native:
        raise ValueError('image inputs require an OpenAI-compatible multimodal provider in this worker')
    initial = None
    if images:
        initial = [{'role': 'system', 'content': system}, {'role': 'user', 'content': [{'type': 'text', 'text': 'Visual evidence for the task follows.'}, *_images(images, root)]}]
    evidence = []
    pending_errors: dict[str, list[int]] = {}
    def record(step):
        if step.kind == 'tool_result':
            from hydra.tool_outcome import failed
            try:
                payload = json.loads(step.content)
            except (ValueError, TypeError):
                payload = {}
            outcome = {key: payload[key] for key in ('ok', 'success', 'isError', 'exit_code', 'returncode', 'timed_out') if isinstance(payload, dict) and key in payload}
            # Match the actual invocation, not merely the tool name or a model's
            # claim that a failure was fixed. Do not copy arguments into receipts.
            operation = hashlib.sha256(json.dumps(
                [step.tool_name, step.tool_arguments], sort_keys=True,
                separators=(',', ':'), ensure_ascii=False).encode()).hexdigest()
            index = len(evidence)
            error = bool(step.tool_error) or failed(payload)
            evidence.append({'tool': step.tool_name, 'tool_call_id': step.tool_call_id,
                             'operation_sha256': operation, 'error': error,
                             'outcome': outcome, 'result_sha256': hashlib.sha256(step.content.encode()).hexdigest()})
            if error:
                pending_errors.setdefault(operation, []).append(index)
            else:
                # A passing retry resolves earlier failures of this exact
                # operation. The failed rows stay intact; the link is a
                # zero-based index into this same evidence list.
                for failed_index in pending_errors.pop(operation, []):
                    evidence[failed_index]['recovered_by'] = index
    started = time.monotonic()
    result = run_observed_turn(AgentLoop(client, model=request['model'], system_prompt=system),
        prompt, tools=tools, max_iterations=request['max_iterations'], max_tokens=2048,
        timeout=min(120, request['timeout_seconds']), initial_messages=initial, on_step=record,
        repo_root=root, requested_provider=request['provider'])
    acceptance = check_acceptance(role=role, root=root, contract=contract, before=before, evidence=evidence, native=native)
    unresolved_errors = [index for index, row in enumerate(evidence)
                         if row['error'] and 'recovered_by' not in row]
    ok = (result.halted_reason == 'natural' and bool(result.final_response.strip())
          and not unresolved_errors and acceptance['status'] == 'passed')
    actual = client.actual_route or {'provider': request['provider'], 'model': request['model'], 'family': request['family']}
    learning, learning_warnings = _learn(root=root, memory_root=request.get('memory_root'), goal=request['goal'],
                                        result=result, provider=actual['provider'], model=actual['model'])
    return {'id': task['id'], 'specialist': role, 'ok': ok, 'verified': False,
            'status': 'completed' if ok else 'incomplete', 'halted_reason': result.halted_reason,
            **({'failure': result.provider_error} if result.provider_error else {}),
            **actual,
            'requested_route': client.requested_route, 'routing_attempts': client.attempts,
            'tool_runtime': 'codex-native-sandbox' if native else 'hydra',
            'response': result.final_response[:24000], 'response_truncated': len(result.final_response) > 24000,
            'acceptance': acceptance, 'library': library_manifest(role),
            'memory': {'recall': recall_receipt, 'learning': learning},
            'memory_warnings': memory_warnings + learning_warnings,
            'evidence': evidence, 'unresolved_tool_errors': unresolved_errors,
            'duration_seconds': round(time.monotonic() - started, 3),
            'iterations': result.iterations, 'tool_calls': result.tool_calls_made}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--request', type=Path, required=True)
    args = parser.parse_args()
    request = read_json(args.request, 131072)
    task_id = request['task']['id']
    if not isinstance(task_id, str) or not task_id.replace('_', '').replace('-', '').isalnum():
        raise ValueError('invalid task ID')
    try:
        result = execute(request, args.request.parent)
    except Exception as exc:
        from hydra.llm import LlmError
        from hydra.provider_health import safe_message
        result = {'id': task_id, 'ok': False, 'verified': False, 'status': 'error',
                  'error': type(exc).__name__, 'message': safe_message(str(exc)),
                  'failure': exc.to_dict() if isinstance(exc, LlmError) else {'kind': 'runtime', 'message': safe_message(str(exc))}}
    from hydra.atomic_write import atomic_write_bytes
    atomic_write_bytes(args.request.parent / f'{task_id}.result.json', json.dumps(result, ensure_ascii=False).encode('utf-8'))
    print(json.dumps({'id': task_id, 'ok': result['ok'], 'status': result['status']}))
    return 0 if result['ok'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
