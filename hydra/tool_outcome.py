"""Interpret explicit tool failure signals without trusting model summaries."""
from __future__ import annotations


def failed(value) -> bool:
    if not isinstance(value, dict):
        return False
    if value.get('ok') is False or value.get('success') is False or value.get('isError') is True or value.get('is_error') is True or value.get('timed_out') is True:
        return True
    if value.get('error'):
        return True
    if any(type(value.get(key)) is int and value[key] != 0 for key in ('exit_code', 'returncode')):
        return True
    return failed(value.get('structuredContent'))
