"""Personal orchestration commands: teams, MCP and public profile packaging."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from hydra.policy import ApprovalDenied
from hydra.llm import LlmError


def register_personal_commands(sub):
    connections = sub.add_parser('connections', help='show official account links and five local OSS retrieval recipes')
    connections.set_defaults(cmd='connections')
    team = sub.add_parser('team', help='plan and execute bounded specialist teams')
    team.set_defaults(cmd='team')
    modes = team.add_subparsers(dest='action', required=True)
    modes.add_parser('list', help='list public specialist profiles')
    plan = modes.add_parser('plan', help='create an editable specialist task graph')
    plan.add_argument('goal')
    plan.add_argument('--provider', required=True)
    plan.add_argument('--model', required=True)
    plan.add_argument('--output', type=Path)
    run = modes.add_parser('run', help='run a configured specialist graph')
    run.add_argument('plan', type=Path)
    run.add_argument('--root', type=Path, default=Path.cwd())
    run.add_argument('--output-root', type=Path, default=Path.home() / '.hydraAgent' / 'team-runs')
    run.add_argument('--private-profiles', type=Path)
    run.add_argument('--env-dir', type=Path)
    run.add_argument('--approval-policy', choices=['allow', 'ask', 'deny'], default='ask')
    show = modes.add_parser('show', help='inspect the public canon and essence for one specialist')
    show.add_argument('specialist')

    mcp = sub.add_parser('mcp', help='serve Hydra tools or connect to configured MCP servers')
    mcp.set_defaults(cmd='mcp')
    modes = mcp.add_subparsers(dest='action', required=True)
    serve = modes.add_parser('serve', help='serve scoped Hydra tools over stdio; read-only by default')
    serve.add_argument('--root', type=Path, default=Path.cwd())
    serve.add_argument('--approval-policy', choices=['deny', 'allow'], default='deny')
    for action in ('tools', 'call'):
        p = modes.add_parser(action)
        p.add_argument('server')
        p.add_argument('--config', type=Path, required=True)
        if action == 'call':
            p.add_argument('tool')
            p.add_argument('--arguments', default='{}', help='JSON object; do not pass secrets in command-line arguments')
            p.add_argument('--approval-policy', choices=['allow', 'ask', 'deny'], default='ask')

    public = sub.add_parser('public-profiles', help='scan and export explicitly selected public profile text')
    public.set_defaults(cmd='public-profiles')
    public.add_argument('--source', type=Path, required=True)
    public.add_argument('--output', type=Path, required=True)
    public.add_argument('--roles', nargs='+', required=True)
    public.add_argument('--private-terms-file', type=Path, help='local JSON list of private literals; never copied into output')

    zapier = sub.add_parser('zapier', help='prepare an official Zapier SDK/CLI/MCP harness')
    zapier.set_defaults(cmd='zapier')
    zapier.add_argument('action', choices=['setup', 'status', 'tools', 'call'])
    zapier.add_argument('--directory', type=Path, required=True)
    zapier.add_argument('--tool', help='SDK tool name for call')
    zapier.add_argument('--arguments', default='{}', help='SDK tool arguments as a JSON object; keep secrets out of argv')
    zapier.add_argument('--approval-policy', choices=['allow', 'ask', 'deny'], default='ask')

    index = sub.add_parser('index', help='incremental source lookup with verified content keys')
    index.set_defaults(cmd='index')
    index.add_argument('action', choices=['refresh', 'search', 'read', 'embed', 'semantic-search'])
    index.add_argument('query', nargs='?', help='search text or a source key')
    index.add_argument('--root', type=Path, default=Path.cwd())
    index.add_argument('--cache', type=Path)
    index.add_argument('--verify', action='store_true', help='rehash every indexed file during refresh')
    index.add_argument('--batch', type=int, default=16, help='maximum source-map embeddings in one finite tick')

    health = sub.add_parser('model-health', help='inspect literal provider states and retry times')
    health.set_defaults(cmd='model-health')
    health.add_argument('--path', type=Path)
    health.add_argument('--provider', help='inspect the configured provider model catalog')
    health.add_argument('--refresh', action='store_true', help='refresh the catalog from its provider')
    health.add_argument('--clear', help='explicitly clear one persisted route/account identity')
    health.add_argument('--env-dir', type=Path)

    residency = sub.add_parser('model-residency', help='explicitly load or unload one local Ollama model')
    residency.set_defaults(cmd='model-residency')
    residency.add_argument('action', choices=['load', 'unload'])
    residency.add_argument('model')
    residency.add_argument('--env-dir', type=Path)

    memory = sub.add_parser('memory-audit', help='inventory duplicates/age and optionally save a recoverable snapshot')
    memory.set_defaults(cmd='memory-audit')
    memory.add_argument('--root', type=Path, required=True)
    memory.add_argument('--stale-days', type=int, default=30)
    memory.add_argument('--archive', type=Path, help='new snapshot directory outside the active tree; originals stay intact')

    maintenance = sub.add_parser('maintenance', help='one finite local maintenance tick; suitable for cron or Task Scheduler')
    maintenance.set_defaults(cmd='maintenance')
    maintenance.add_argument('action', choices=['enqueue', 'tick', 'status'])
    maintenance.add_argument('--queue', type=Path, default=Path.home() / '.hydraAgent' / 'maintenance.sqlite')
    maintenance.add_argument('--operation', choices=['index', 'memory_audit'])
    maintenance.add_argument('--root', type=Path)
    maintenance.add_argument('--key', help='unique scheduled-slot key; repeating it does not duplicate a job')

    tribunal = sub.add_parser('tribunal', help='run configured blind cross-family review with canon and essence')
    tribunal.set_defaults(cmd='tribunal')
    tribunal.add_argument('plan', type=Path)
    tribunal.add_argument('--root', type=Path, default=Path.cwd())
    tribunal.add_argument('--output-root', type=Path, default=Path.home() / '.hydraAgent' / 'tribunal-runs')
    tribunal.add_argument('--private-profiles', type=Path)
    tribunal.add_argument('--env-dir', type=Path)


def cmd_personal(args) -> int:
    try:
        if args.cmd == 'connections':
            from hydra.connections import catalog
            result = catalog()
        elif args.cmd == 'team':
            from hydra.specialists import catalog, read_json, system_prompt
            from hydra.teams import make_plan, run_team
            if args.action == 'list':
                result = {'specialists': catalog()}
            elif args.action == 'show':
                print(system_prompt(args.specialist))
                return 0
            elif args.action == 'plan':
                result = make_plan(args.goal, args.provider, args.model)
                if args.output:
                    from hydra.atomic_write import atomic_write_bytes
                    atomic_write_bytes(args.output, (json.dumps(result, indent=2) + '\n').encode(), overwrite=False)
            else:
                result = run_team(read_json(args.plan, 131072), root=args.root, output_root=args.output_root,
                                  approval_policy=args.approval_policy, private_root=args.private_profiles, env_dir=args.env_dir)
        elif args.cmd == 'mcp':
            if args.action == 'serve':
                from hydra.mcp_server import serve
                serve(args.root, approval_policy=args.approval_policy)
                return 0
            from hydra.mcp_bridge import bind_mcp_tools
            from hydra.policy import ApprovalPolicy
            policy = ApprovalPolicy(getattr(args, 'approval_policy', 'ask'))
            tools = {t.name: t for t in bind_mcp_tools(args.config, policy)}
            result = tools['mcp_tools'].invoke(server=args.server) if args.action == 'tools' else tools['mcp_call'].invoke(
                server=args.server, tool=args.tool, arguments=json.loads(args.arguments))
        elif args.cmd == 'public-profiles':
            from hydra.public_profiles import export_profiles
            terms = json.loads(args.private_terms_file.read_text(encoding='utf-8')) if args.private_terms_file else []
            if not isinstance(terms, list) or not all(isinstance(t, str) for t in terms):
                raise ValueError('private terms file must contain a JSON string list')
            result = export_profiles(args.source, args.output, args.roles, private_terms=tuple(terms))
        elif args.cmd == 'index':
            from hydra.source_index import SourceIndex
            index = SourceIndex(args.root, cache=args.cache)
            if args.action in {'embed', 'semantic-search'}:
                from hydra.source_semantic import fill, search
                result = fill(index, batch=args.batch) if args.action == 'embed' else search(index, args.query)
            else:
                result = index.refresh(verify=args.verify) if args.action == 'refresh' else (
                    index.search(args.query) if args.action == 'search' else index.read(args.query))
        elif args.cmd == 'model-health':
            from hydra.provider_health import HealthStore
            if args.clear and (args.provider or args.refresh):
                raise ValueError('--clear cannot be combined with catalog inspection')
            if args.refresh and not args.provider:
                raise ValueError('--refresh requires --provider')
            if args.clear:
                HealthStore(args.path).clear(args.clear)
                result = HealthStore(args.path).snapshot()
            elif args.provider:
                from hydra.provider_health import catalog_report
                result = catalog_report(args.provider, env_dir=args.env_dir, health_path=args.path, refresh=args.refresh)
            else:
                result = HealthStore(args.path).snapshot()
        elif args.cmd == 'model-residency':
            from hydra.model_residency import residency
            result = residency(args.action, args.model, env_dir=args.env_dir)
        elif args.cmd == 'memory-audit':
            from hydra.memory_audit import audit
            result = audit(args.root, stale_days=args.stale_days, archive=args.archive)
        elif args.cmd == 'maintenance':
            from hydra.maintenance_queue import MaintenanceQueue
            queue = MaintenanceQueue(args.queue)
            if args.action == 'enqueue':
                if not args.root:
                    raise ValueError('maintenance enqueue requires --root, --operation and --key')
                result = queue.enqueue(args.operation, args.root, args.key)
            else:
                result = queue.tick() if args.action == 'tick' else queue.snapshot()
        elif args.cmd == 'tribunal':
            from hydra.tribunal import run_tribunal
            from hydra.specialists import read_json
            result = run_tribunal(read_json(args.plan, 131072), root=args.root, output_root=args.output_root,
                                  private_root=args.private_profiles, env_dir=args.env_dir)
        else:
            from hydra.zapier_harness import setup, status, sdk_call
            if args.action == 'setup':
                result = setup(args.directory)
            elif args.action == 'status':
                result = status(args.directory)
            else:
                if args.action == 'call' and not args.tool:
                    raise ValueError('zapier call requires --tool')
                result = sdk_call(args.directory, tool=args.tool if args.action == 'call' else None,
                                  arguments=json.loads(args.arguments), approval_policy=args.approval_policy)
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 1 if result.get('isError') or result.get('verdict') in {'RED', 'fail', 'inconclusive'} or result.get('status') in {'degraded', 'not_installed', 'failed', 'error', 'unavailable', 'lease_lost'} else 0
    except (ValueError, OSError, RuntimeError, ImportError, ApprovalDenied, LlmError) as exc:
        print(f'{args.cmd}: {type(exc).__name__}: {exc}', file=sys.stderr)
        return 2
