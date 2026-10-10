"""Prepare pinned official Zapier SDK dependencies, without credentials or copies."""
from __future__ import annotations

import json
from pathlib import Path
import shutil

SDK_VERSION = '0.115.1'
CLI_VERSION = '0.86.7'


def setup(directory: Path) -> dict:
    from hydra.specialists import canonical_path
    directory = canonical_path(directory)
    if directory.exists():
        raise ValueError('Zapier harness directory must be new; existing files are never overwritten')
    node = shutil.which('node') or 'node'
    package = {'name': 'hydra-zapier-harness', 'private': True, 'type': 'module',
               'scripts': {'zapier': 'zapier-sdk', 'mcp': 'zapier-sdk mcp'},
               'dependencies': {'@zapier/zapier-sdk': SDK_VERSION, '@zapier/zapier-sdk-cli': CLI_VERSION}}
    config = {'servers': {'zapier': {'command': node,
        'args': [str(directory / 'node_modules' / '@zapier' / 'zapier-sdk-cli' / 'bin' / 'zapier-sdk.mjs'), 'mcp'],
        'timeout': 60, 'read_only_tools': []}}}
    directory.mkdir(parents=True)
    (directory / 'package.json').write_text(json.dumps(package, indent=2) + '\n', encoding='utf-8')
    (directory / 'mcp.json').write_text(json.dumps(config, indent=2) + '\n', encoding='utf-8')
    (directory / '.gitignore').write_text('node_modules/\n.env*\n.zapier*\n', encoding='utf-8')
    return {'status': 'prepared', 'directory': str(directory), 'config': str(directory / 'mcp.json'),
            'next_steps': ['In the harness directory: npm install --ignore-scripts',
                           'Authenticate with your own account: npm run zapier -- login',
                           'Set HYDRA_MCP_CONFIG to the generated mcp.json, then use hydra ask or hydra mcp tools.'],
            'sdk_version': SDK_VERSION, 'cli_version': CLI_VERSION,
            'license_note': 'The official SDK packages use Zapier terms; they are not relicensed as Hydra MIT code.',
            'cost_note': 'SDK open beta is currently free; hosted MCP and connected apps may have task limits or charges. No account connected or action performed.'}
