"""Personal orchestration commands: teams, MCP and public profile packaging."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from hydra.policy import ApprovalDenied


def register_personal_commands(sub):
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
    zapier.add_argument('action', choices=['setup'])
    zapier.add_argument('--directory', type=Path, required=True)


def cmd_personal(args) -> int:
    try:
        if args.cmd == 'team':
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
        else:
            from hydra.zapier_harness import setup
            result = setup(args.directory)
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 1 if result.get('isError') or result.get('verdict') == 'RED' else 0
    except (ValueError, OSError, RuntimeError, ImportError, ApprovalDenied) as exc:
        print(f'{args.cmd}: {type(exc).__name__}: {exc}', file=sys.stderr)
        return 2
