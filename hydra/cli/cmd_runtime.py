"""Runtime visibility CLI command handlers."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from hydra.declarative_runtime import build_runtime_brief, doctor_runtime_catalog, load_runtime_catalog
from hydra.llm import LlmError
from hydra.providers import (
    DEFAULT_ENV_DIR,
    ProviderError,
    list_providers as list_providers_fn,
    make_client,
)
from hydra.roles import RoleError, RoleSpec, provider_available, resolve_roles
from hydra.setup import prompt_setup


REPO_ROOT = Path(__file__).resolve().parents[2]
HYDRA_CONFIG = REPO_ROOT / ".hydraAgent" / "hydra.yaml"
HYDRA_SCHEMA = REPO_ROOT / ".hydraAgent" / "schemas" / "hydra.schema.yaml"


def register_runtime_commands(sub: argparse._SubParsersAction) -> None:
    sub.add_parser("providers", help="list configured providers")

    p_models = sub.add_parser("models", help="list models the provider exposes")
    p_models.add_argument("--provider", default="ollama")
    p_models.add_argument("--env-dir", default=None, help=f"provider env dir (default: {DEFAULT_ENV_DIR})")
    p_models.add_argument(
        "--setup-if-needed",
        action="store_true",
        help="prompt for local/cloud/Codex setup if provider config is missing",
    )

    p_roles = sub.add_parser(
        "roles",
        help="show planner/doer/auditor model routing",
    )
    p_roles.add_argument("--config", default=None)
    p_roles.add_argument("--env-dir", default=None)

    p_declarative = sub.add_parser("declarative", help="inspect declarative runtime contracts")
    declarative_sub = p_declarative.add_subparsers(dest="declarative_cmd", required=True)
    p_declarative_brief = declarative_sub.add_parser("brief", help="print declarative runtime brief JSON")
    p_declarative_brief.add_argument("prompt")
    p_declarative_brief.add_argument("--root", default=str(REPO_ROOT))
    p_declarative_doctor = declarative_sub.add_parser("doctor", help="validate declarative runtime wiring")
    p_declarative_doctor.add_argument("--root", default=str(REPO_ROOT))


def _make_client_or_setup(args: argparse.Namespace):
    env_dir = getattr(args, "env_dir", None)
    try:
        return make_client(args.provider, env_dir=env_dir)
    except ProviderError as e:
        if not getattr(args, "setup_if_needed", False):
            print(
                f"provider error: {e}\n"
                f"Run `python3 -m hydra setup --provider {args.provider}` "
                f"or pass --setup-if-needed.",
                file=sys.stderr,
            )
            raise
        if not sys.stdin.isatty():
            print(
                f"provider error: {e}\n"
                "--setup-if-needed requires an interactive terminal.",
                file=sys.stderr,
            )
            raise
        print(f"Provider {args.provider!r} needs setup. Starting setup.", file=sys.stderr)
        prompt_setup(env_dir=env_dir)
        return make_client(args.provider, env_dir=env_dir)


def cmd_providers(_args: argparse.Namespace) -> int:
    names = list_providers_fn()
    print("Built-in providers (operator-named providers also load from .env.<name>):")
    for name in names:
        print(f"  {name}")
    return 0


def cmd_models(args: argparse.Namespace) -> int:
    try:
        client, cfg = _make_client_or_setup(args)
    except ProviderError:
        return 2
    try:
        names = client.list_models()
    except LlmError as e:
        print(f"could not list models for {cfg.name!r}: {e}", file=sys.stderr)
        if cfg.name != "ollama" and cfg.model:
            print(
                f"configured default model: {cfg.model}",
                file=sys.stderr,
            )
        return 1
    if not names:
        print(f"no models reported by {cfg.name!r}")
        return 0
    for name in names:
        print(name)
    return 0


def _resolved_roles_or_exit(args: argparse.Namespace):
    try:
        return resolve_roles(
            config_path=args.config or HYDRA_CONFIG,
            schema_path=HYDRA_SCHEMA,
        )
    except RoleError as e:
        print(f"role error: {e}", file=sys.stderr)
        return None


def _print_role(role: RoleSpec, env_dir: str | None = None) -> None:
    available, detail = provider_available(role, env_dir=env_dir)
    state = "available" if available else "unavailable"
    print(
        f"{role.role}: provider={role.provider} model={role.model} "
        f"family={role.family} status={state} detail={detail}"
    )


def cmd_roles(args: argparse.Namespace) -> int:
    roles = _resolved_roles_or_exit(args)
    if roles is None:
        return 2
    _print_role(roles.planner, args.env_dir)
    _print_role(roles.doer, args.env_dir)
    _print_role(roles.auditor, args.env_dir)
    return 0


def cmd_declarative(args: argparse.Namespace) -> int:
    root = Path(args.root).expanduser().resolve()
    catalog = load_runtime_catalog(root)
    if args.declarative_cmd == "brief":
        brief = build_runtime_brief(args.prompt, catalog, root=root)
        print(json.dumps(brief, indent=2, sort_keys=True))
        return 0
    if args.declarative_cmd == "doctor":
        report = doctor_runtime_catalog(catalog)
        print(json.dumps(report, indent=2, sort_keys=True))
        return 0
    print(f"unknown declarative command: {args.declarative_cmd}", file=sys.stderr)
    return 2
