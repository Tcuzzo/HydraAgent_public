"""Local selectors available to any chat-model family; they never execute tools."""
from __future__ import annotations

import json
import os


def laya_decide(state, questions):
    from hydra.decision_models import LayaDecisionClient
    if len(json.dumps({'state': state, 'questions': questions}).encode()) > 65536:
        raise ValueError('local decision input exceeds 64 KiB')
    return {'status': 'completed', 'backend': 'laya', 'executed_actions': False,
            'result': LayaDecisionClient().predict(state, questions, timeout=60)}


def needle_select(query, candidates, tools):
    from hydra.llm import ChatMessage
    from hydra.needle_client import NeedleClient
    if not isinstance(query, str) or not query.strip() or len(query) > 8000:
        raise ValueError('Needle query must be nonempty and <=8000 characters')
    if not isinstance(candidates, list) or not 1 <= len(candidates) <= 32 or not all(isinstance(x, str) for x in candidates):
        raise ValueError('select 1..32 tool names')
    available = {t.name: t for t in tools if t.name not in {'needle_select', 'laya_decide'}}
    if set(candidates) - available.keys():
        raise ValueError('Needle candidate is not in the current tool catalog')
    schemas = [{'type': 'function', 'function': {'name': name, 'description': available[name].description,
                'parameters': available[name].parameters}} for name in dict.fromkeys(candidates)]
    model = os.environ.get('HYDRA_NEEDLE_MODEL', 'needle3')
    response = NeedleClient(model=model).chat([ChatMessage('user', query)], model=model, tools=schemas, timeout=60)
    return {'status': 'completed', 'backend': 'needle', 'model': model, 'executed_actions': False,
            'selections': [{'tool': call.name, 'arguments': call.arguments} for call in response.tool_calls],
            'resource_budget': response.raw.get('resource_budget'),
            'next_step': 'These are proposals. Invoke the selected tool through the normal runtime policy.'}
