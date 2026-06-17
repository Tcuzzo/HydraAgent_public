"""hydra doctor — check dependency versions + known vulnerabilities; --fix upgrades.

Read-only by default: reports each dependency's installed vs latest version
(PyPI) and any known advisories (OSV), plus pip and the Python runtime. `--fix`
upgrades the outdated/vulnerable packages to the latest, for end-user safety.
"""
from __future__ import annotations

import argparse
import json
import platform
import subprocess
import sys

from hydra import doctor


def register_doctor_command(sub) -> None:
    p = sub.add_parser(
        "doctor",
        help="check dependency versions + known security advisories (--fix upgrades them)",
    )
    p.add_argument("--fix", action="store_true",
                   help="upgrade outdated/vulnerable packages (and pip) to the latest")
    p.add_argument("--no-pip", action="store_true", help="skip checking pip itself")
    p.add_argument("--timeout", type=float, default=10.0,
                   help="network timeout per lookup in seconds (default: 10)")
    p.add_argument("--format", choices=("text", "json"), default="text")


def _report_dict(r) -> dict:
    return {
        "name": r.name,
        "installed": r.installed,
        "latest": r.latest,
        "outdated": r.outdated,
        "vulnerable": r.vulnerable,
        "vulns": r.vulns,
    }


def _status(r) -> str:
    if r.installed is None:
        return "not installed"
    if r.vulnerable:
        return "VULNERABLE: " + ", ".join(r.vulns)
    if r.outdated:
        return f"outdated -> {r.latest}"
    return "ok"


def cmd_doctor(args: argparse.Namespace) -> int:
    names = doctor.hydra_dependency_names()
    if not getattr(args, "no_pip", False):
        names.append("pip")
    names = list(dict.fromkeys(names))  # dedupe, preserve order
    if not names:
        names = ["pip"]

    reports = doctor.diagnose(
        names,
        installed_of=doctor.installed_version,
        latest_of=lambda n: doctor.pypi_latest(n, timeout=args.timeout),
        vulns_of=lambda n, v: doctor.osv_vulns(n, v, timeout=args.timeout),
    )
    summary = doctor.summarize(reports)
    offline = all(r.latest is None for r in reports if r.installed)

    if args.format == "json":
        print(json.dumps({
            "python": platform.python_version(),
            "platform": platform.platform(),
            "offline": offline,
            "summary": summary,
            "packages": [_report_dict(r) for r in reports],
        }, indent=2))
    else:
        print(f"hydra doctor · Python {platform.python_version()} · "
              f"{platform.system()} {platform.machine()}")
        print(f"{'package':24}{'installed':14}{'latest':14}status")
        print("-" * 70)
        for r in sorted(reports, key=lambda r: r.name.lower()):
            print(f"{r.name:24}{(r.installed or '-'):14}{(r.latest or '?'):14}{_status(r)}")
        print(f"\n{summary['total']} checked · {summary['outdated']} outdated · "
              f"{summary['vulnerable']} vulnerable")
        if offline:
            print("note: could not reach PyPI/OSV — version & vulnerability checks were skipped.")

    fixes = doctor.packages_to_fix(reports)

    if not fixes:
        if args.format == "text" and not offline:
            print("All dependencies are current and free of known vulnerabilities. ✓")
        return 0

    if args.fix:
        print(f"\nUpgrading: {' '.join(fixes)}", flush=True)
        rc = subprocess.call(doctor.upgrade_command(fixes))
        if rc != 0:
            print("hydra doctor: upgrade failed — run the pip command above manually.",
                  file=sys.stderr)
            return rc
        print("Upgrade complete. Re-run `hydra doctor` to confirm.", flush=True)
        return 0

    if args.format == "text":
        print(f"\nRun `hydra doctor --fix` to upgrade: {' '.join(fixes)}")
    return 1 if summary["vulnerable"] else 0
