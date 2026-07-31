"""CLI: what already looks like this? (Stage AN 3/3)

Haroon's bin/cyg-similar equivalent — a thin command-line wrapper around
mc.cyg_similar() (Stage AN 2/3), which merges symbol_index and doc_index
hits ranked by similarity.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import mc


def main() -> None:
    parser = argparse.ArgumentParser(
        description="List the most similar indexed symbols/docs to this query."
    )
    parser.add_argument("query", nargs="+", help="What you're comparing against")
    parser.add_argument("--project", default=None, help="Restrict the search to one project")
    parser.add_argument("--limit", type=int, default=5, help="Max results (default 5)")
    args = parser.parse_args()
    query = " ".join(args.query)

    try:
        mc.init_db()
        results = mc.cyg_similar(query, project=args.project, limit=args.limit)
    except Exception as exc:
        print(f"[cyg-similar] error: {exc}")
        return

    print(f"[cyg-similar] query: {query!r}")
    if not results:
        print("[cyg-similar] nothing indexed yet - run bin/bbb_graph_ingest.py first")
        return

    for hit in results:
        label = hit.get("symbol_name") or hit.get("title") or hit["file_path"]
        print(
            f"  [{hit['source_table']}] {label} ({hit['file_path']}) "
            f"- similarity={hit['similarity']:.3f}"
        )


if __name__ == "__main__":
    main()
