"""One isolated specialist execution; parent dispatcher owns the hard deadline."""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
from pathlib import Path
import time

from hydra.specialists import READ_TOOLS, WRITERS, read_json, system_prompt, canonical_path


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


def execute(request: dict, run_dir: Path) -> dict:
    from hydra.cli.tool_binding import bind_tools
    from hydra.loop import AgentLoop
    from hydra.providers import make_client
    task = request['task']
    role = task['specialist']
    root = canonical_path(Path(request['root']))
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
    if len(prompt) > 80000:
        raise ValueError('dependency context exceeds 80000 characters; split the plan')
    system = system_prompt(role, private_root=request.get('private_root'))
    client, cfg = make_client(request['provider'], env_dir=request.get('env_dir'))
    policy = request['approval_policy'] if role in WRITERS else 'deny'
    tools = bind_tools(root, approval_policy=policy, read_only_mcp=role not in WRITERS)
    if role not in WRITERS:
        tools = [t for t in tools if t.name in READ_TOOLS or t.name in {'mcp_servers', 'mcp_tools', 'mcp_call'}]
    native = cfg.name == 'codex'
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
    def record(step):
        if step.kind == 'tool_result':
            from hydra.tool_outcome import failed
            try:
                payload = json.loads(step.content)
            except (ValueError, TypeError):
                payload = {}
            outcome = {key: payload[key] for key in ('ok', 'success', 'isError', 'exit_code', 'returncode', 'timed_out') if isinstance(payload, dict) and key in payload}
            evidence.append({'tool': step.tool_name, 'error': bool(step.tool_error) or failed(payload),
                             'outcome': outcome, 'result_sha256': hashlib.sha256(step.content.encode()).hexdigest()})
    started = time.monotonic()
    result = AgentLoop(client, model=request['model'], system_prompt=system).run(
        prompt, tools=tools, max_iterations=request['max_iterations'], max_tokens=2048,
        timeout=min(120, request['timeout_seconds']), initial_messages=initial, on_step=record,
        repo_root=root, requested_provider=request['provider'])
    ok = result.halted_reason == 'natural' and bool(result.final_response.strip()) and not any(e['error'] for e in evidence)
    return {'id': task['id'], 'specialist': role, 'ok': ok, 'verified': False,
            'status': 'completed' if ok else 'incomplete', 'halted_reason': result.halted_reason,
            'provider': request['provider'], 'model': request['model'], 'family': request['family'],
            'tool_runtime': 'codex-native-sandbox' if native else 'hydra',
            'response': result.final_response[:24000], 'response_truncated': len(result.final_response) > 24000,
            'evidence': evidence, 'duration_seconds': round(time.monotonic() - started, 3),
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
        result = {'id': task_id, 'ok': False, 'verified': False, 'status': 'error',
                  'error': type(exc).__name__, 'message': 'Worker failed; verify provider, inputs and local configuration.'}
    from hydra.atomic_write import atomic_write_bytes
    atomic_write_bytes(args.request.parent / f'{task_id}.result.json', json.dumps(result, ensure_ascii=False).encode('utf-8'))
    print(json.dumps({'id': task_id, 'ok': result['ok'], 'status': result['status']}))
    return 0 if result['ok'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
