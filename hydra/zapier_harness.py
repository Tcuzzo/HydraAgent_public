"""Prepare pinned official Zapier SDK dependencies, without credentials or copies."""
from __future__ import annotations

import json
from pathlib import Path
import shutil
import os

SDK_VERSION = '0.115.1'
CLI_VERSION = '0.86.7'


def _launcher(entry: Path) -> str:
    return ('process.env.ZAPIER_MAX_NETWORK_RETRIES = "0";\n'
            'process.env.ZAPIER_APPROVAL_MODE = "throw";\n'
            'process.env.ZAPIER_OPEN_AUTO_MODE_APPROVALS_IN_BROWSER = "false";\n'
            f'await import({json.dumps(entry.as_uri())});\n')


def setup(directory: Path) -> dict:
    from hydra.specialists import canonical_path
    directory = canonical_path(directory)
    if directory.exists():
        raise ValueError('Zapier harness directory must be new; existing files are never overwritten')
    node = shutil.which('node') or 'node'
    package = {'name': 'hydra-zapier-harness', 'private': True, 'type': 'module',
               'scripts': {'zapier': 'zapier-sdk', 'mcp': 'node sdk.mjs mcp'},
               'dependencies': {'@zapier/zapier-sdk': SDK_VERSION, '@zapier/zapier-sdk-cli': CLI_VERSION}}
    config = {'servers': {'zapier': {'command': node,
        'args': [str(directory / 'sdk.mjs'), 'mcp'],
        'timeout': 60, 'read_only_tools': []}}}
    directory.mkdir(parents=True)
    (directory / 'package.json').write_text(json.dumps(package, indent=2) + '\n', encoding='utf-8')
    (directory / 'mcp.json').write_text(json.dumps(config, indent=2) + '\n', encoding='utf-8')
    (directory / 'sdk.mjs').write_text(_launcher(directory / 'node_modules' / '@zapier' / 'zapier-sdk-cli' / 'bin' / 'zapier-sdk.mjs'), encoding='utf-8')
    (directory / '.gitignore').write_text('node_modules/\n.env*\n.zapier*\n', encoding='utf-8')
    return {'status': 'prepared', 'directory': str(directory), 'config': str(directory / 'mcp.json'),
            'next_steps': ['In the harness directory: npm install --ignore-scripts',
                           'Authenticate with your own account: npm run zapier -- login',
                           'Set HYDRA_MCP_CONFIG to the generated mcp.json, then use hydra ask or hydra mcp tools.'],
            'sdk_version': SDK_VERSION, 'cli_version': CLI_VERSION,
            'license_note': 'The official SDK packages use Zapier terms; they are not relicensed as Hydra MIT code.',
            'cost_note': 'SDK open beta is currently free; hosted MCP and connected apps may have task limits or charges. No account connected or action performed.'}


def status(directory: Path) -> dict:
    from hydra.specialists import canonical_path, read_json
    directory = canonical_path(directory)
    versions = {}
    for name, expected in [('zapier-sdk', SDK_VERSION), ('zapier-sdk-cli', CLI_VERSION)]:
        path = directory / 'node_modules' / '@zapier' / name / 'package.json'
        if not path.is_file():
            return {'status': 'not_installed', 'next_step': f'Run npm install --ignore-scripts in {directory}'}
        versions[name] = read_json(path, 131072).get('version')
        if versions[name] != expected:
            raise ValueError(f'installed {name} does not match the reviewed version {expected}')
    return {'status': 'installed', 'versions': versions, 'transport': 'official_sdk_local_stdio',
            'hosted_mcp': False, 'authenticated': 'not_checked',
            'links': {'login': 'https://docs.zapier.com/sdk', 'connections': 'https://zapier.com/app/connections',
                      'rates': 'https://zapier.com/pricing/rates'},
            'next_steps': ['npm run zapier -- login', 'npm run zapier -- list-connections',
                           'npm run zapier -- create-connection <app>'],
            'cost_context': 'SDK actions are free in beta as checked 2026-10-10; third-party fees and future pricing are external.'}


def sdk_call(directory: Path, *, tool=None, arguments=None, approval_policy='ask'):
    """Use the installed official SDK only; never redirect to hosted Zapier MCP."""
    from hydra.mcp_bridge import bind_mcp_tools
    from hydra.policy import ApprovalPolicy
    from hydra.specialists import canonical_path
    import tempfile
    checked = status(directory)
    if checked['status'] != 'installed':
        return checked
    directory = canonical_path(directory)
    entry = directory / 'node_modules' / '@zapier' / 'zapier-sdk-cli' / 'bin' / 'zapier-sdk.mjs'
    if not entry.is_file() or not shutil.which('node'):
        raise ValueError('SDK entrypoint or Node is missing')
    with tempfile.TemporaryDirectory(prefix='hydra-sdk-') as temporary:
        launcher = Path(temporary) / 'sdk.mjs'
        launcher.write_text(_launcher(entry), encoding='utf-8')
        credential_names = ('ZAPIER_TOKEN', 'ZAPIER_CREDENTIALS', 'ZAPIER_CREDENTIALS_CLIENT_ID', 'ZAPIER_CREDENTIALS_CLIENT_SECRET', 'APPDATA', 'LOCALAPPDATA', 'USERPROFILE', 'HOME')
        configuration = {'servers': {'zapier': {'command': shutil.which('node'), 'args': [str(launcher), 'mcp'],
                                               'env': {key: key for key in credential_names if key in os.environ},
                                               'timeout': 60, 'read_only_tools': []}}}
        path = Path(temporary) / 'sdk.json'
        path.write_text(json.dumps(configuration), encoding='utf-8')
        bindings = {t.name: t for t in bind_mcp_tools(path, ApprovalPolicy(approval_policy))}
        try:
            result = bindings['mcp_tools'].invoke(server='zapier') if tool is None else bindings['mcp_call'].invoke(
                server='zapier', tool=tool, arguments=arguments or {})
        except RuntimeError:
            return {'status': 'error', 'isError': True, 'outcome': 'unknown' if tool else 'unavailable',
                    'retry_safe': tool is None, 'transport': checked['transport'], 'hosted_mcp': False,
                    'next_step': 'Inspect the connected app before retrying an action; it may already have completed.' if tool else 'Check the local SDK installation and account connection.'}
    return {'status': 'error' if result.get('isError') else 'completed', 'isError': bool(result.get('isError')),
            'result': result, 'transport': checked['transport'], 'hosted_mcp': False,
            'retry_safe': tool is None, 'cost_context': checked['cost_context']}
