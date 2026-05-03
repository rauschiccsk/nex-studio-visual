"""RAG query CLI — semantic search over ICC Knowledge Base (Qdrant + Ollama).

Usage:
    poetry run python scripts/rag_query.py "your query text"
    poetry run python scripts/rag_query.py "your query" --tenant icc --limit 5
    poetry run python scripts/rag_query.py "your query" --json
"""

from __future__ import annotations

import argparse
import json
import os
import sys

import httpx
from qdrant_client import QdrantClient

QDRANT_URL = os.environ.get("QDRANT_URL", "http://localhost:9130")
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:9132")
EMBED_MODEL = os.environ.get("EMBED_MODEL", "nomic-embed-text")
REQUEST_TIMEOUT = 30


def embed(text: str) -> list[float]:
    response = httpx.post(
        f"{OLLAMA_URL}/api/embeddings",
        json={"model": EMBED_MODEL, "prompt": text},
        timeout=REQUEST_TIMEOUT,
    )
    response.raise_for_status()
    return response.json()["embedding"]


def search(query: str, tenant: str, limit: int) -> list[dict]:
    client = QdrantClient(url=QDRANT_URL, timeout=REQUEST_TIMEOUT)
    vector = embed(query)
    response = client.query_points(
        collection_name=tenant,
        query=vector,
        limit=limit,
        with_payload=True,
    )
    return [
        {
            "score": round(float(hit.score), 4),
            "source_file": (hit.payload or {}).get("source_file") or (hit.payload or {}).get("filename", ""),
            "content": (hit.payload or {}).get("content", ""),
        }
        for hit in response.points
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description="Semantic search over ICC Knowledge Base.")
    parser.add_argument("query", help="Query text")
    parser.add_argument("--tenant", default="icc", help="Qdrant collection (default: icc)")
    parser.add_argument("--limit", type=int, default=5, help="Top-K results (default: 5)")
    parser.add_argument("--json", action="store_true", help="Output raw JSON")
    args = parser.parse_args()

    try:
        results = search(args.query, args.tenant, args.limit)
    except httpx.HTTPError as exc:
        print(f"HTTP error talking to Ollama/Qdrant: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(results, indent=2, ensure_ascii=False))
        return 0

    if not results:
        print(f"No matches in tenant '{args.tenant}'.")
        return 0

    print(f"Top {len(results)} matches in '{args.tenant}' for: {args.query!r}\n")
    for i, r in enumerate(results, 1):
        preview = r["content"].strip().replace("\n", " ")
        if len(preview) > 200:
            preview = preview[:197] + "..."
        print(f"[{i}] score={r['score']}  source={r['source_file']}")
        print(f"    {preview}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
