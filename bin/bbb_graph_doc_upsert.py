"""CLI: re-index just one file (Stage AN 3/3).

Lighter than bin/bbb_graph_ingest.py's full rescan — re-indexes a single
.py (via mc.upsert_symbol_file) or .md (via mc.upsert_doc_file) file, for
"I just edited this one file" workflows.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import mc


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Re-index a single .py or .md file instead of a full scan."
    )
    parser.add_argument("file_path", help="Path to the .py or .md file to re-index")
    args = parser.parse_args()

    path = Path(args.file_path)
    if not path.exists():
        print(f"[bbb-graph-doc-upsert] error: file not found: {path}")
        return

    try:
        mc.init_db()
        if path.suffix == ".py":
            count = mc.upsert_symbol_file(path)
            print(f"[bbb-graph-doc-upsert] indexed {count} symbol(s) from {path}")
        elif path.suffix == ".md":
            mc.upsert_doc_file(path)
            print(f"[bbb-graph-doc-upsert] indexed {path} into doc_index")
        else:
            print(
                f"[bbb-graph-doc-upsert] unsupported extension {path.suffix!r} "
                "(only .py/.md for now)"
            )
    except Exception as exc:
        print(f"[bbb-graph-doc-upsert] error: {exc}")


if __name__ == "__main__":
    main()
