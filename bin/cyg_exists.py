"""CLI: does something like this already exist? (Stage AN 3/3)

Haroon's bin/cyg-exists equivalent — a thin command-line wrapper around
mc.cyg_exists() (Stage AN 2/3), the checkable form of the "reuse before
build" Golden Rule.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import mc


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Check whether something like this already exists in symbol_index/doc_index."
    )
    parser.add_argument("query", nargs="+", help="What you're about to build/write")
    parser.add_argument("--project", default=None, help="Restrict the search to one project")
    parser.add_argument(
        "--threshold", type=float, default=0.6, help="Similarity threshold (default 0.6)"
    )
    parser.add_argument(
        "--decision",
        choices=["reuse", "build_new", "no_match"],
        default=None,
        help="Record what you did with this check as a reuse receipt (requires --session)",
    )
    parser.add_argument("--session", default=None, help="Session id, required with --decision")
    args = parser.parse_args()
    query = " ".join(args.query)

    if args.decision and not args.session:
        print("[cyg-exists] error: --decision requires --session")
        return

    try:
        mc.init_db()
        result = mc.cyg_exists(query, project=args.project, threshold=args.threshold)
    except Exception as exc:
        print(f"[cyg-exists] error: {exc}")
        return

    print(f"[cyg-exists] query: {query!r}")
    if result["score"] is None:
        print("[cyg-exists] nothing indexed yet - run bin/bbb_graph_ingest.py first")
        if args.decision:
            mc.log_reuse_decision(args.session, query, args.decision, match=None)
        return

    print(
        f"[cyg-exists] exists: {result['exists']} "
        f"(score={result['score']:.3f}, threshold={args.threshold})"
    )
    if result["match"]:
        match = result["match"]
        label = match.get("symbol_name") or match.get("title") or match["file_path"]
        print(f"[cyg-exists] match: {match['source_table']} - {label} ({match['file_path']})")

    if args.decision:
        mc.log_reuse_decision(args.session, query, args.decision, match=result["match"])


if __name__ == "__main__":
    main()
