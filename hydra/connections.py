"""Local retrieval connection recipes and account setup links. No auto-login."""
from importlib.util import find_spec


SOURCES = [
    {'id': 'qdrant', 'package': 'qdrant-client', 'module': 'qdrant_client', 'license': 'Apache-2.0',
     'source': 'https://github.com/qdrant/qdrant-client', 'setup': 'QdrantClient(path="./qdrant-data")',
     'purpose': 'Persistent local vector search; supply your local embeddings.'},
    {'id': 'chroma', 'package': 'chromadb', 'module': 'chromadb', 'license': 'Apache-2.0',
     'source': 'https://github.com/chroma-core/chroma', 'setup': 'chromadb.PersistentClient(path="./chroma-data")',
     'purpose': 'Embedded vector collections; explicitly provide embeddings.'},
    {'id': 'pgvector', 'package': 'pgvector', 'module': 'pgvector', 'license': 'PostgreSQL',
     'source': 'https://github.com/pgvector/pgvector', 'setup': 'Install the pgvector extension in your existing local PostgreSQL service.',
     'purpose': 'Transactional vector storage alongside SQL provenance.'},
    {'id': 'neo4j-community', 'package': 'neo4j', 'module': 'neo4j', 'license': 'GPL-3.0 (Community server)',
     'source': 'https://github.com/neo4j/neo4j', 'setup': 'Run the Community server locally; configure its Bolt URL and credentials outside source.',
     'purpose': 'Explicit entity and relationship traversal.'},
    {'id': 'graphiti', 'package': 'graphiti-core', 'module': 'graphiti_core', 'license': 'Apache-2.0',
     'source': 'https://github.com/getzep/graphiti', 'setup': 'Use the upstream Ollama example with local embedding and reranking clients plus a supported graph database.',
     'purpose': 'Temporal knowledge graphs; requires extraction-quality evaluation.'},
]


def catalog():
    rows = []
    for item in SOURCES:
        rows.append({**item, 'dependency_installed': find_spec(item['module']) is not None,
                     'status': 'setup_recipe', 'runtime_backend_wired': False})
    return {'sources': rows, 'default_memory': 'local SQLite + optional sqlite-vec',
            'note': 'These are five complementary open-source setup recipes, not a benchmark ranking or five running services. Install only what you need.',
            'account_links': {'zapier_sdk': 'https://docs.zapier.com/sdk', 'zapier_connections': 'https://zapier.com/app/connections',
                              'twingate': 'https://www.twingate.com/docs/connectors-on-linux',
                              'cloudflare': 'https://dash.cloudflare.com/',
                              'cloudflare_agent_payments': 'https://developers.cloudflare.com/agents/tools/payments/'}}
