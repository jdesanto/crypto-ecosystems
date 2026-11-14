#!/usr/bin/env python3
"""Run a SPARQL query over taxonomy + ontology + overlay + derived facts.

  python3 ontology/scripts/query.py ontology/queries/q04-evm-networks.rq
  python3 ontology/scripts/query.py ontology/queries/q02-aggregation-policies.rq --full

Without --full the ecosystem graph is used (no repositories, ~55k triples).
With --full, build/ecosystems-full.nt.gz is loaded (~2.2M triples, ~20s).
The derivation rules are re-run on every invocation so results match the
current overlay. No reasoner is involved: queries spell out sub-classes and
sub-properties with property paths.
"""

from __future__ import annotations

import argparse
import gzip
import subprocess
import sys
from pathlib import Path

import pyoxigraph as ox

ONT = Path(__file__).resolve().parent.parent
BUILD = ONT / "build"
MODULES = ["ceo.ttl", "vocab/categories.ttl", "vocab/repo-roles.ttl", "vocab/technologies.ttl",
           "overlay/groupings.ttl", "overlay/entities.ttl"]


def build_store(full: bool) -> ox.Store:
    raw = BUILD / ("ecosystems-full.nt.gz" if full else "ecosystems.nt")
    if not raw.exists():
        cmd = [sys.executable, str(ONT / "scripts" / "toml_to_rdf.py")] + ([] if full else ["--no-repos"])
        subprocess.run(cmd, check=True)
    store = ox.Store()
    if raw.suffix == ".gz":
        with gzip.open(raw, "rb") as fh:
            store.bulk_load(input=fh, format=ox.RdfFormat.N_TRIPLES)
    else:
        store.bulk_load(path=str(raw), format=ox.RdfFormat.N_TRIPLES)
    for rel in MODULES:
        store.bulk_load(path=str(ONT / rel), format=ox.RdfFormat.TURTLE)
    derived = []
    for rq in sorted((ONT / "rules").glob("*.rq")):
        derived.extend(store.query(rq.read_text()))
    for t in derived:
        store.add(ox.Quad(t.subject, t.predicate, t.object))
    return store


def render(term) -> str:
    if term is None:
        return ""
    value = term.value
    for prefix, ns in (("eco:", "https://w3id.org/cryptoeco/id/ecosystem/"),
                       ("ceo:", "https://w3id.org/cryptoeco/ontology#")):
        if value.startswith(ns):
            return prefix + value[len(ns):]
    return value


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("query", type=Path)
    ap.add_argument("--full", action="store_true", help="include repositories (full graph)")
    ap.add_argument("--limit", type=int, default=50, help="max rows to print (default 50)")
    ap.add_argument("--width", type=int, default=60, help="max column width (default 60)")
    args = ap.parse_args()

    store = build_store(args.full)
    result = store.query(args.query.read_text())
    if isinstance(result, bool):
        print(result)
        return 0
    header = [v.value for v in result.variables]
    rows = [[render(t)[: args.width] for t in sol] for sol in result]
    widths = [max([len(h)] + [len(r[i]) for r in rows]) for i, h in enumerate(header)]
    print("  ".join(h.ljust(w) for h, w in zip(header, widths)))
    print("  ".join("-" * w for w in widths))
    for r in rows[: args.limit]:
        print("  ".join(c.ljust(w) for c, w in zip(r, widths)))
    if len(rows) > args.limit:
        print(f"... {len(rows) - args.limit} more rows")
    print(f"({len(rows)} rows)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
