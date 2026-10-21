"""Local selectors available to any chat-model family; they never execute tools."""
from __future__ import annotations

import json


def laya_decide(state, questions):
    from hydra.decision_models import LayaDecisionClient
    if len(json.dumps({'state': state, 'questions': questions}).encode()) > 65536:
        raise ValueError('local decision input exceeds 64 KiB')
    return {'status': 'completed', 'backend': 'laya', 'executed_actions': False,
            'result': LayaDecisionClient().predict(state, questions, timeout=60)}


def kev_decide(state, questions):
    from hydra.decision_models import KevDecisionClient
    if len(json.dumps({'state': state, 'questions': questions}).encode()) > 65536:
        raise ValueError('local decision input exceeds 64 KiB')
    return {'status': 'completed', 'backend': 'kev', 'executed_actions': False,
            'result': KevDecisionClient().predict(state, questions, timeout=120)}


def needle_select(query, candidates, tools):
    from hydra.llm import ChatMessage
    from hydra.needle_client import NeedleClient, configured_needle_model
    if not isinstance(query, str) or not query.strip() or len(query) > 8000:
        raise ValueError('Needle query must be nonempty and <=8000 characters')
    if not isinstance(candidates, list) or not 1 <= len(candidates) <= 32 or not all(isinstance(x, str) for x in candidates):
        raise ValueError('select 1..32 tool names')
    available = {t.name: t for t in tools if t.name not in {'needle_select', 'laya_decide', 'kev_decide'}}
    if set(candidates) - available.keys():
        raise ValueError('Needle candidate is not in the current tool catalog')
    schemas = [{'type': 'function', 'function': {'name': name, 'description': available[name].description,
                'parameters': available[name].parameters}} for name in dict.fromkeys(candidates)]
    model = configured_needle_model()
    response = NeedleClient(model=model).chat([ChatMessage('user', query)], model=model, tools=schemas, timeout=60)
    return {'status': 'completed', 'backend': 'needle', 'model': model, 'executed_actions': False,
            'selections': [{'tool': call.name, 'arguments': call.arguments} for call in response.tool_calls],
            'confidence': response.raw.get('confidence'),
            'confidence_available': response.raw.get('confidence') is not None,
            'resource_budget': response.raw.get('resource_budget'),
            'next_step': 'These are proposals. Invoke the selected tool through the normal runtime policy.'}
