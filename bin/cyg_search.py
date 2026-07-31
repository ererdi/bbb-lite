"""CLI: search symbols, docs, and memory in one shot (Stage AN 3/3).

Shows all three of mc.py's search surfaces (search_symbols, search_docs,
search_memory) under separate headings, since they cover different things:
code, project docs, and memory/*.md notes.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import mc


def _print_section(title: str, hits: list[dict], formatter) -> None:
    print(f"--- {title} ---")
    if not hits:
        print("  (no matches)")
        return
    for hit in hits:
        print(f"  {formatter(hit)}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Search symbol_index, doc_index, and memory_index for a query."
    )
    parser.add_argument("query", nargs="+", help="What to search for")
    parser.add_argument("--limit", type=int, default=3, help="Max results per source (default 3)")
    args = parser.parse_args()
    query = " ".join(args.query)

    mc.init_db()

    try:
        symbol_hits = mc.search_symbols(query, limit=args.limit)
    except Exception as exc:
        print(f"[cyg-search] symbol search error: {exc}")
        symbol_hits = []

    try:
        doc_hits = mc.search_docs(query, limit=args.limit)
    except Exception as exc:
        print(f"[cyg-search] doc search error: {exc}")
        doc_hits = []

    try:
        memory_hits = mc.search_memory(query, limit=args.limit)
    except Exception as exc:
        print(f"[cyg-search] memory search error: {exc}")
        memory_hits = []

    print(f"[cyg-search] query: {query!r}")
    _print_section(
        "symbols",
        symbol_hits,
        lambda h: f"{h['symbol_name']} ({h['file_path']}) - similarity={h['similarity']:.3f}",
    )
    _print_section(
        "docs",
        doc_hits,
        lambda h: f"{h['title']} ({h['file_path']}) - similarity={h['similarity']:.3f}",
    )
    _print_section(
        "memory",
        memory_hits,
        lambda h: f"{h['title']} ({h['source_file']}) - confidence={h['confidence']:.2f}",
    )


if __name__ == "__main__":
    main()
