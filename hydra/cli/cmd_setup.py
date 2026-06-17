"""Setup CLI command handler."""
from __future__ import annotations

import argparse
import sys

from hydra.setup import (
    AUTOMATION_POLICIES,
    SUDO_POLICIES,
    CommissioningReport,
    SetupError,
    prompt_setup,
    setup_cloud_provider,
    setup_codex_oauth,
    setup_local_ollama,
    setup_operator_policy,
)


def register_setup_commands(sub: argparse._SubParsersAction, *, default_env_dir: object) -> None:
    p_setup = sub.add_parser(
        "setup",
        help="configure local Ollama, cloud/API providers, and Codex OAuth path",
        description=(
            "Configure HydraAgent outside the repo. Supports local Ollama, "
            "cloud/OpenAI-compatible API providers, and Codex evaluator OAuth path."
        ),
    )
    p_setup.add_argument(
        "--mode",
        choices=("interactive", "local", "cloud", "codex"),
        default="interactive",
    )
    p_setup.add_argument("--provider", default="ollama")
    p_setup.add_argument("--endpoint", default=None)
    p_setup.add_argument("--model", default=None)
    p_setup.add_argument("--api-key", default=None)
    p_setup.add_argument("--codex-oauth-path", default=None)
    p_setup.add_argument("--env-dir", default=None, help=f"provider env dir (default: {default_env_dir})")
    p_setup.add_argument(
        "--automation-policy",
        choices=sorted(AUTOMATION_POLICIES),
        default="ask",
        help="record first-run automation posture: ask, auto, or yolo",
    )
    p_setup.add_argument(
        "--sudo-policy",
        choices=sorted(SUDO_POLICIES),
        default="ask",
        help="record first-run sudo posture: ask, never, or yolo",
    )
    p_setup.add_argument(
        "--gpu",
        choices=("yes", "no", "unknown"),
        default="unknown",
        help="record whether this setup expects local GPU acceleration",
    )
    p_setup.add_argument(
        "--download-consent",
        choices=("yes", "no"),
        default="no",
        help="record whether setup may ask to download dependencies/models",
    )
    p_setup.add_argument(
        "--launch-tui",
        choices=("yes", "no"),
        default="no",
        help="record whether setup should hand off to the interactive session",
    )
    p_setup.add_argument(
        "--non-interactive",
        action="store_true",
        help="do not prompt; require all values via flags",
    )


def cmd_setup(args: argparse.Namespace) -> int:
    try:
        if not args.non_interactive and args.mode == "interactive":
            results = prompt_setup(env_dir=args.env_dir)
        elif args.mode == "local":
            results = [
                setup_operator_policy(
                    has_local_gpu=_gpu_arg(args.gpu),
                    automation_policy=args.automation_policy,
                    sudo_policy=args.sudo_policy,
                    download_consent=args.download_consent == "yes",
                    launch_tui=args.launch_tui == "yes",
                    env_dir=args.env_dir,
                ),
                setup_local_ollama(
                    endpoint=args.endpoint or "http://localhost:11434",
                    model=args.model or "qwen3:8b",
                    api_key=args.api_key,
                    env_dir=args.env_dir,
                ),
            ]
        elif args.mode == "cloud":
            results = [
                setup_operator_policy(
                    has_local_gpu=_gpu_arg(args.gpu),
                    automation_policy=args.automation_policy,
                    sudo_policy=args.sudo_policy,
                    download_consent=args.download_consent == "yes",
                    launch_tui=args.launch_tui == "yes",
                    env_dir=args.env_dir,
                ),
                setup_cloud_provider(
                    args.provider,
                    endpoint=args.endpoint,
                    model=args.model,
                    api_key=args.api_key,
                    env_dir=args.env_dir,
                ),
            ]
        elif args.mode == "codex":
            results = [
                setup_operator_policy(
                    has_local_gpu=_gpu_arg(args.gpu),
                    automation_policy=args.automation_policy,
                    sudo_policy=args.sudo_policy,
                    download_consent=args.download_consent == "yes",
                    launch_tui=args.launch_tui == "yes",
                    env_dir=args.env_dir,
                ),
                setup_codex_oauth(
                    oauth_path=args.codex_oauth_path,
                    env_dir=args.env_dir,
                ),
            ]
        else:
            raise SetupError("--mode must be interactive, local, cloud, or codex")
    except SetupError as e:
        print(f"setup error: {e}", file=sys.stderr)
        return 2

    _print_setup_results(results)
    return 0


def _print_setup_results(results) -> None:
    if isinstance(results, CommissioningReport):
        print(f"setup evidence: {results.evidence_path}")
        print(f"env dir: {results.env_dir}")
        print(f"local gpu: {results.has_local_gpu}")
        print(f"automation policy: {results.automation_policy}")
        print(f"sudo policy: {results.sudo_policy}")
        if results.download_consent:
            print("downloads: operator consent recorded")
        else:
            print("downloads: skipped; some local features may be unavailable")
        for res in results.results:
            keys = ", ".join(res.keys_written)
            print(f"configured {res.provider}: {res.path} ({keys})")
        for item in results.skipped:
            print(f"skipped: {item}")
        for link in results.links:
            print(f"setup link: {link}")
        if results.tui_command:
            print(f"launch TUI: {results.tui_command}")
        return
    for res in results:
        keys = ", ".join(res.keys_written)
        print(f"configured {res.provider}: {res.path} ({keys})")


def _gpu_arg(value: str) -> bool | None:
    if value == "yes":
        return True
    if value == "no":
        return False
    return None
