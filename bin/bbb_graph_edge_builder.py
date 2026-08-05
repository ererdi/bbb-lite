"""CLI: draft a Haroon-format edges.jsonl from this project's own indexed
"calls"/"references" edges (Stage BUS-2 prep, mc.export_sync_edges()).

Target when proposed to haroon-bbb-sync: scripts/bbb-graph-edge-builder.py,
writing to their ~/.bbb/graph/edges.jsonl (the source load_edges() reads in
lib/bbb_graph_edges.py). Kept as a draft under bin/ here and NOT pushed to
their repo yet - see decisions.md, 2026-08-05, Stage BUS-2.

Does not touch SQL directly - mc.py stays the only file that does (CLAUDE.md
rule). This script only calls mc.index_symbols()/index_docs() (detection)
and mc.export_sync_edges() (read-back-out), then writes the jsonl file.

Writes to graph/edges-draft-for-haroon.jsonl by default - deliberately NOT
graph/edges.jsonl, which is this project's OWN curated edge source read by
mc.load_graph_edges() with curated=True. Mixing hundreds of auto-derived,
uncurated calls/references edges into that file would misrepresent them as
human-trusted.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import mc

DEFAULT_OUTPUT = Path(__file__).resolve().parent.parent / "graph" / "edges-draft-for-haroon.jsonl"


def build_edges_jsonl(
    root_dir: str | Path | None = None,
    *,
    output_path: str | Path = DEFAULT_OUTPUT,
    skip_dirs: list[str] | None = None,
    db_path: Path = mc.DB_PATH,
) -> int:
    root = Path(root_dir) if root_dir is not None else Path(__file__).resolve().parent.parent
    mc.init_db(db_path)
    mc.index_symbols(root_dir=root, db_path=db_path)
    mc.index_docs(root_dir=root, skip_dirs=skip_dirs, db_path=db_path)
    edges = mc.export_sync_edges(project=root.name, db_path=db_path)

    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as f:
        for edge in edges:
            f.write(json.dumps(edge, ensure_ascii=False) + "\n")
    return len(edges)


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Draft a Haroon-format edges.jsonl from this project's calls/"
            "references edges (does not write graph/edges.jsonl)."
        )
    )
    parser.add_argument(
        "--root", default=None,
        help="Project root to index (default: bbb-lite itself)",
    )
    parser.add_argument(
        "--out", default=str(DEFAULT_OUTPUT),
        help=f"Output .jsonl path (default: {DEFAULT_OUTPUT})",
    )
    parser.add_argument(
        "--db", default=str(mc.DB_PATH),
        help="Database to index into/read from (default: this project's own mc.db)",
    )
    parser.add_argument(
        "--skip-dirs", default="memory",
        help=(
            "Comma-separated top-level dir names to skip when indexing docs "
            "(default: 'memory' - bbb-lite's own memory_index convention, "
            "not necessarily right for another repo; pass '' for none)"
        ),
    )
    args = parser.parse_args()

    skip_dirs = [name for name in args.skip_dirs.split(",") if name.strip()]
    count = build_edges_jsonl(
        args.root, output_path=args.out, skip_dirs=skip_dirs, db_path=Path(args.db)
    )
    print(f"[bbb-graph-edge-builder] wrote {count} edge(s) to {args.out}")


if __name__ == "__main__":
    main()
