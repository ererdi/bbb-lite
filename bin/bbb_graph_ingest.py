"""CLI: re-index everything at once (Stage AN 3/3).

A manually-triggered, bulk "rescan everything" command: symbols
(index_symbols), project docs (index_docs), and memory notes
(index_memory_files), in that order. Haroon's bulk graph-ingest equivalent.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import mc


def main() -> None:
    mc.init_db()
    total = 0
    for label, fn in (
        ("symbol", mc.index_symbols),
        ("doc", mc.index_docs),
        ("memory", mc.index_memory_files),
    ):
        try:
            count = fn()
        except Exception as exc:
            print(f"[bbb-graph-ingest] {label} indexing error: {exc}")
            continue
        print(f"[bbb-graph-ingest] indexed {count} {label} item(s)")
        total += count
    print(f"[bbb-graph-ingest] total: {total} item(s) indexed")


if __name__ == "__main__":
    main()
