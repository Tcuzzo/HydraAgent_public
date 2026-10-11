"""Explicit public-only profile export. Detection is not an IP clearance proof."""
from __future__ import annotations

import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import shutil
import tempfile
import unicodedata

from hydra.specialists import ROLES, read_json, reject_links


class PublicProfileError(ValueError):
    pass


def scan_text(text: str, *, private_terms: tuple[str, ...] = ()) -> list[str]:
    categories = set()
    text = unicodedata.normalize('NFKC', text)
    patterns = {
        'email': r'[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}',
        'credential': r'(?i)(?:-----BEGIN .*PRIVATE KEY-----|\b(?:sk|ghp|github_pat|xox[baprs])[-_][\w-]{12,}|(?:api[_-]?key|password|secret|access[_-]?token)\s*[\"\x27]?\s*[:=]\s*[\"\x27]?[^\s,}\"\x27]{4,}|https?://[^\s/@]+:[^\s/@]+@)',
        'private_path': r'(?i)(?:[A-Z]:[\\/](?:Users|Documents and Settings)[\\/]|/(?:home|Users)/[^\s/]+)',
    }
    for category, pattern in patterns.items():
        if re.search(pattern, text):
            categories.add(category)
    if re.search(r'(?i)\bBearer\s+\S{8,}|\bgh[osru]_[\w]{12,}|\bAKIA[A-Z0-9]{16}\b', text):
        categories.add('credential')
    for candidate in re.findall(r'(?<![\w])(?:\d{1,3}\.){3}\d{1,3}(?![\w])|(?<![\w])(?:[0-9a-fA-F]*:){2,}[0-9a-fA-F:.%]+', text):
        try:
            ipaddress.ip_address(candidate.split('%')[0])
            categories.add('ip_address')
        except ValueError:
            pass
    if any(unicodedata.normalize('NFKC', term).casefold() in text.casefold() for term in private_terms if term):
        categories.add('private_literal')
    return sorted(categories)


def export_profiles(source: str | Path, destination: str | Path, roles: list[str], *, private_terms: tuple[str, ...] = ()) -> dict:
    source, destination = Path(source), Path(destination)
    reject_links(destination)
    if not roles or len(set(roles)) != len(roles) or any(role not in ROLES for role in roles):
        raise PublicProfileError('export requires an explicit list of known public role IDs')
    if destination.exists() or destination.is_symlink():
        raise PublicProfileError('export destination must not exist')
    staged = {}
    rows = []
    for role in sorted(roles):
        try:
            data = read_json(source / f'{role}.json')
        except (OSError, ValueError, UnicodeError):
            raise PublicProfileError(f'{role}: invalid source profile') from None
        if set(data) - {'id', 'edition', 'canon', 'essence', 'license', 'credits'}:
            raise PublicProfileError(f'{role}: unsupported public fields')
        if data.get('id') != role or any(not isinstance(data.get(key), str) or not data[key].strip() for key in ('canon', 'essence', 'license')):
            raise PublicProfileError(f'{role}: invalid public profile')
        if not isinstance(data.get('credits'), list) or not all(isinstance(c, str) for c in data['credits']):
            raise PublicProfileError(f'{role}: public credits must be a list of strings')
        if data.get('edition') != 'generic-public':
            raise PublicProfileError(f'{role}: source must be an explicitly authored generic-public edition')
        content = (json.dumps(data, ensure_ascii=False, sort_keys=True, indent=2) + '\n').encode('utf-8')
        texts = [value for value in data.values() if isinstance(value, str)] + data['credits']
        findings = sorted({category for text in texts for category in scan_text(text, private_terms=private_terms)})
        if findings:
            raise PublicProfileError(f'{role}: public export blocked ({", ".join(findings)})')
        staged[f'{role}.json'] = content
        rows.append({'id': role, 'file': f'{role}.json', 'sha256': hashlib.sha256(content).hexdigest()})
    manifest = {'schema': 'hydra.public_profiles.v1', 'profiles': rows,
                'review_note': 'Pattern scan passed; semantic IP and publication review is still required.'}
    destination.parent.mkdir(parents=True, exist_ok=True)
    reject_links(destination)
    temporary = Path(tempfile.mkdtemp(prefix='.hydra-export-', dir=destination.parent))
    try:
        for filename, content in staged.items():
            (temporary / filename).write_bytes(content)
        (temporary / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n', encoding='utf-8')
        if destination.exists():
            raise PublicProfileError('export destination already exists')
        reject_links(destination)
        os.rename(temporary, destination)
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)
    return manifest
