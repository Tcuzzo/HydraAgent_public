"""Bundle only the public declarative contracts, never workspace state."""
from pathlib import Path
from shutil import copyfile

from setuptools import setup
from setuptools.command.build_py import build_py


PUBLIC_CONTRACTS = (
    "tools/registry.yaml", "tools/filesystem.yaml", "tools/skill-library.yaml",
    "tools/subagents.yaml", "tools/shell.yaml", "tools/web.yaml", "tools/system.yaml",
    "skills/index.yaml", "ux/response-contracts.yaml",
    "policies/danger-gates.yaml", "policies/trust-tiers.yaml",
    "policies/backs-invariants.yaml", "policies/sniper-testing.yaml",
    "policies/anti-mock-theater.yaml", "playbooks/dev-mode-elite-build.yaml",
)


class BuildPublicRuntime(build_py):
    def run(self):
        super().run()
        source = Path(__file__).resolve().parent / ".hydraAgent"
        destination = Path(self.build_lib) / "hydra/runtime_data/catalog"
        for relative in PUBLIC_CONTRACTS:
            target = destination / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            copyfile(source / relative, target)


setup(cmdclass={"build_py": BuildPublicRuntime})
