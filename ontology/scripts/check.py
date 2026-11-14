#!/usr/bin/env python3
"""Validate the ontology against the taxonomy data and report curation status.

  python3 ontology/scripts/check.py                 # everything
  python3 ontology/scripts/check.py --skip-reasoner # no Java / HermiT
  python3 ontology/scripts/check.py --rebuild       # re-run the converter first

Steps
  1. parse every module (rdflib and Oxigraph must both accept it)
  2. vocabulary hygiene: no label shared by two concepts of one scheme
  3. overlay integrity: every eco: IRI used in overlay/ exists in the taxonomy
  4. run rules/*.rq over taxonomy + overlay      -> build/derived.ttl
  5. OWL 2 DL consistency and inferences (HermiT via owlready2; needs Java)
  6. SHACL validation (pyshacl)
  7. coverage report and curation queue          -> build/curation-queue.tsv

Exits 1 on a parse error, dangling IRI, duplicate label, inconsistency or
SHACL violation. SHACL warnings are reported, not fatal.
"""

from __future__ import annotations

import argparse
import collections
import csv
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pyoxigraph as ox
import rdflib
from rdflib.namespace import RDF, RDFS, SKOS

ONT = Path(__file__).resolve().parent.parent
BUILD = ONT / "build"
MODULES = [
    "ceo.ttl",
    "vocab/categories.ttl",
    "vocab/repo-roles.ttl",
    "vocab/technologies.ttl",
    "overlay/groupings.ttl",
    "overlay/entities.ttl",
]
OVERLAY = ["overlay/groupings.ttl", "overlay/entities.ttl"]
SHAPES = "shapes.ttl"
RAW = BUILD / "ecosystems.nt"

CEO = "https://w3id.org/cryptoeco/ontology#"
ECO = "https://w3id.org/cryptoeco/id/ecosystem/"
PREFIXES = {
    "ceo": CEO,
    "eco": ECO,
    "cat": "https://w3id.org/cryptoeco/vocab/category/",
    "role": "https://w3id.org/cryptoeco/vocab/repo-role/",
    "tech": "https://w3id.org/cryptoeco/id/technology/",
    "rdfs": str(RDFS),
    "skos": str(SKOS),
}
TAXONOMY_PREDICATES = {CEO + p for p in ("hasSubEcosystem", "subEcosystemOf", "hasRepository",
                                         "repositoryOf", "hasCodeHostAccount", "ownedByAccount")}
SPARQL_PREFIXES = "".join(f"PREFIX {k}: <{v}>\n" for k, v in PREFIXES.items()) + \
    "PREFIX rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#>\n"

failures: list[str] = []


def section(title: str) -> None:
    print(f"\n== {title} " + "=" * max(0, 70 - len(title)))


def fail(msg: str) -> None:
    failures.append(msg)
    print(f"  FAIL  {msg}")


def short(iri: str) -> str:
    for p, ns in PREFIXES.items():
        if iri.startswith(ns):
            return f"{p}:{iri[len(ns):]}"
    return iri


def rows(store: ox.Store, query: str):
    return [tuple(t.value if t is not None else None for t in sol) for sol in store.query(SPARQL_PREFIXES + query)]


# --------------------------------------------------------------------------- steps

def parse_modules() -> rdflib.Graph:
    section("1. Parse modules")
    merged = rdflib.Graph()
    for rel in MODULES + [SHAPES]:
        path = ONT / rel
        try:
            g = rdflib.Graph().parse(path, format="turtle")
            ox.Store().bulk_load(path=str(path), format=ox.RdfFormat.TURTLE)
        except Exception as exc:  # noqa: BLE001 - report any parser error
            fail(f"{rel}: {exc}")
            continue
        print(f"  ok    {rel:28s} {len(g):5d} triples")
        if rel != SHAPES:
            merged += g
    return merged


def vocabulary_hygiene(modules: rdflib.Graph) -> None:
    section("2. Vocabulary hygiene")
    from toml_to_rdf import normalize_label  # same normalisation as the tag resolver

    for scheme in modules.subjects(RDF.type, SKOS.ConceptScheme):
        labels: dict[str, set] = collections.defaultdict(set)
        concepts = set(modules.subjects(SKOS.inScheme, scheme))
        for c in concepts:
            if (c, SKOS.prefLabel, None) not in modules:
                fail(f"{short(str(c))} has no skos:prefLabel")
            for prop in (SKOS.prefLabel, SKOS.altLabel, SKOS.hiddenLabel):
                for label in modules.objects(c, prop):
                    labels[normalize_label(str(label))].add(short(str(c)))
        dupes = {k: v for k, v in labels.items() if len(v) > 1}
        for label, cs in dupes.items():
            fail(f"label '{label}' is shared by {sorted(cs)} in {short(str(scheme))}")
        print(f"  {short(str(scheme)):22s} {len(concepts):3d} concepts, {len(labels):3d} distinct labels, "
              f"{len(dupes)} clashes")


def load_store(rebuild: bool) -> ox.Store:
    if rebuild or not RAW.exists():
        print(f"  building {RAW.relative_to(ONT.parent)} ...")
        subprocess.run([sys.executable, str(ONT / "scripts" / "toml_to_rdf.py"), "--no-repos"], check=True)
    store = ox.Store()
    store.bulk_load(path=str(RAW), format=ox.RdfFormat.N_TRIPLES)
    for rel in MODULES:
        store.bulk_load(path=str(ONT / rel), format=ox.RdfFormat.TURTLE)
    return store


def overlay_integrity(store: ox.Store) -> None:
    section("3. Overlay integrity")
    known = {r[0] for r in rows(store, "SELECT ?e WHERE { ?e a ceo:Ecosystem }")}
    used: set[str] = set()
    for rel in OVERLAY:
        g = rdflib.Graph().parse(ONT / rel, format="turtle")
        for term in set(g.subjects()) | set(g.objects()):
            if isinstance(term, rdflib.URIRef) and str(term).startswith(ECO):
                used.add(str(term))
    dangling = sorted(used - known)
    for iri in dangling:
        fail(f"overlay refers to {short(iri)}, which is not a taxonomy node")
    print(f"  {len(used)} taxonomy nodes referenced by overlay/, {len(dangling)} dangling")


def run_rules(store: ox.Store) -> ox.Store:
    section("4. Derivation rules")
    derived = ox.Store()
    for rq in sorted((ONT / "rules").glob("*.rq")):
        t0 = time.time()
        triples = list(store.query(rq.read_text()))
        for t in triples:
            derived.add(ox.Quad(t.subject, t.predicate, t.object))
        print(f"  {rq.name:42s} {len(triples):6d} triples  {time.time() - t0:5.2f}s")
    BUILD.mkdir(exist_ok=True)
    derived.dump(str(BUILD / "derived.ttl"), ox.RdfFormat.TURTLE, from_graph=ox.DefaultGraph(), prefixes=PREFIXES)
    print(f"  {len(derived):,} distinct derived triples -> {(BUILD / 'derived.ttl').relative_to(ONT.parent)}")
    for q in derived:
        store.add(q)
    return derived


def reason(derived: ox.Store) -> None:
    section("5. OWL 2 DL reasoning (HermiT)")
    if shutil.which("java") is None:
        print("  skipped: java not found")
        return
    import owlready2

    # The raw sub-ecosystem links only entail ceo:Ecosystem and ceo:rollsUpInto, which carries
    # no constraints, so they cannot cause an inconsistency; leaving them out keeps this ~5s.
    graph = ox.Store()
    for rel in MODULES:
        graph.bulk_load(path=str(ONT / rel), format=ox.RdfFormat.TURTLE)
    for q in derived:
        graph.add(q)
    path = BUILD / "reasoner-input.nt"
    graph.dump(str(path), ox.RdfFormat.N_TRIPLES, from_graph=ox.DefaultGraph())

    world = owlready2.World()
    onto = world.get_ontology(path.as_uri()).load(format="ntriples")
    t0 = time.time()
    try:
        with onto:
            owlready2.sync_reasoner_hermit(world, infer_property_values=True, debug=0)
    except owlready2.OwlReadyInconsistentOntologyError:
        fail("ontology + overlay + derived facts are INCONSISTENT (check disjoint types and ranges)")
        return
    print(f"  consistent ({len(graph):,} triples, {time.time() - t0:.1f}s)")
    unsat = [c.iri for c in world.inconsistent_classes() if str(c.iri).startswith(CEO)]
    for c in unsat:
        fail(f"unsatisfiable class {short(c)}")

    def members(name: str) -> list[str]:
        cls = world.search_one(iri=CEO + name)
        return sorted(i.name for i in cls.instances()) if cls else []

    print("  inferred members of defined classes:")
    for name in ("Layer3", "EVMCompatibleNetwork", "SVMNetwork", "MoveNetwork", "NativeAsset",
                 "DataAvailabilityProvider", "OracleProvider"):
        m = members(name)
        print(f"    {name:26s} {len(m):3d}  {', '.join(m[:8])}{' ...' if len(m) > 8 else ''}")
    langs = collections.Counter()
    chain = world.search_one(iri=CEO + "supportsLanguage")
    for _subject, lang in chain.get_relations() if chain else ():
        langs[lang.name] += 1
    print("  ceo:supportsLanguage inferred via property chain: "
          + ", ".join(f"{k} on {v} entities" for k, v in langs.most_common()))


def shacl(store: ox.Store) -> None:
    section("6. SHACL validation")
    from pyshacl import validate

    data = rdflib.Graph()
    data.parse(data=store.dump(format=ox.RdfFormat.N_TRIPLES, from_graph=ox.DefaultGraph()), format="nt")
    shapes = rdflib.Graph().parse(ONT / SHAPES, format="turtle")
    t0 = time.time()
    _, report, _ = validate(data, shacl_graph=shapes, inference="none", allow_warnings=True)
    SH = rdflib.Namespace("http://www.w3.org/ns/shacl#")
    groups: dict[tuple[str, str], list[str]] = collections.defaultdict(list)
    for result in report.subjects(RDF.type, SH.ValidationResult):
        severity = str(report.value(result, SH.resultSeverity)).rsplit("#", 1)[-1]
        path = report.value(result, SH.resultPath)
        message = str(report.value(result, SH.resultMessage) or "")
        what = f"{short(str(path)) if path else ''} {message}".strip()
        groups[(severity, what)].append(short(str(report.value(result, SH.focusNode))))
    counts = collections.Counter()
    for (severity, what), nodes in sorted(groups.items(), key=lambda kv: (kv[0][0] != "Violation", -len(kv[1]))):
        counts[severity] += len(nodes)
        line = f"{len(nodes):4d} x {what[:90]}  [{', '.join(sorted(nodes)[:5])}{' ...' if len(nodes) > 5 else ''}]"
        if severity == "Violation":
            fail(line)
        else:
            print(f"  {severity:7s} {line}")
    print(f"  {counts['Violation']} violations, {counts['Warning']} warnings ({time.time() - t0:.1f}s)")


def coverage(store: ox.Store, derived: ox.Store) -> None:
    section("7. Coverage and curation queue")
    titles = dict(rows(store, "SELECT ?e ?t WHERE { ?e a ceo:Ecosystem ; rdfs:label ?t }"))
    files = dict(rows(store, "SELECT ?e ?f WHERE { ?e a ceo:Ecosystem ; ceo:sourceFile ?f }"))
    typed = {r[0] for r in rows(store, "SELECT DISTINCT ?e WHERE { ?e a ceo:Ecosystem ; a/rdfs:subClassOf* ceo:CryptoEntity }")}
    groupings = {r[0] for r in rows(store, "SELECT DISTINCT ?e WHERE { ?e a/rdfs:subClassOf* ceo:EcosystemGrouping }")}
    criteria = {r[0] for r in rows(store, "SELECT DISTINCT ?g WHERE { ?g ceo:groupingCriterion ?c }")}
    edges = rows(store, "SELECT ?p ?c WHERE { ?p ceo:hasSubEcosystem ?c }")
    n = len(titles)
    print(f"  nodes: {len(typed):,} typed as entities, {len(groupings):,} groupings, "
          f"{n - len(typed) - len(groupings):,} untyped  (of {n:,})")

    typed_links: dict[frozenset, set[str]] = collections.defaultdict(set)
    for s, p, o in rows(store, "SELECT ?s ?p ?o WHERE { ?s ?p ?o . FILTER(STRSTARTS(STR(?p), STR(ceo:))) "
                               "FILTER(STRSTARTS(STR(?s), STR(eco:)) && STRSTARTS(STR(?o), STR(eco:))) }"):
        if p not in TAXONOMY_PREDICATES:
            typed_links[frozenset((s, o))].add(p)
    buckets = collections.Counter()
    for parent, child in edges:
        preds = typed_links.get(frozenset((parent, child)), set())
        if preds - {CEO + "buildsOn"}:
            buckets["specific typed relation"] += 1
        elif parent in criteria:
            buckets["explained by grouping criterion"] += 1
        elif preds:
            buckets["generic ceo:buildsOn (fallback)"] += 1
        elif parent in groupings:
            buckets["catch-all grouping (no criterion)"] += 1
        else:
            buckets["uninterpreted"] += 1
    print(f"  raw sub-ecosystem links ({len(edges):,}):")
    for k, v in buckets.most_common():
        print(f"    {v:5d}  {100 * v / len(edges):5.1f}%  {k}")

    # Proposals: annotations from G4/G6. A proposal on a typed node it does not refine is a disagreement.
    supers: dict[str, set[str]] = collections.defaultdict(set)
    for c, s in rows(store, "SELECT ?c ?s WHERE { ?c rdfs:subClassOf* ?s . ?c a owl:Class . FILTER(isIRI(?s)) }"
                     .replace("owl:Class", "<http://www.w3.org/2002/07/owl#Class>")):
        supers[c].add(s)
    asserted: dict[str, set[str]] = collections.defaultdict(set)
    for e, t in rows(store, "SELECT ?e ?t WHERE { ?e a ?t . ?e a ceo:Ecosystem . FILTER(?t != ceo:Ecosystem) }"):
        asserted[e].add(t)
    proposals: dict[str, set[str]] = collections.defaultdict(set)
    for q in derived:
        if q.predicate.value == CEO + "proposedType":
            proposals[q.subject.value].add(q.object.value)
    disagreements = []
    for node, props in proposals.items():
        for prop in props:
            for t in asserted.get(node, ()):
                if prop not in supers.get(t, set()) and t not in supers.get(prop, set()):
                    disagreements.append((node, t, prop))
    print(f"  proposed types: {sum(len(v) for v in proposals.values())} on {len(proposals)} nodes, "
          f"{len(disagreements)} disagree with a curated type:")
    for node, t, prop in sorted(disagreements):
        print(f"    {titles.get(node, node)!r} is {short(t)}, but a grouping proposes {short(prop)}")

    children = collections.Counter(p for p, _ in edges)
    parents = collections.Counter(c for _, c in edges)
    BUILD.mkdir(exist_ok=True)
    out = BUILD / "curation-queue.tsv"
    with out.open("w", newline="") as fh:
        w = csv.writer(fh, delimiter="\t")
        w.writerow(["status", "title", "iri", "source_file", "children", "parents", "proposed_types"])
        queue = []
        for node in titles:
            if node in groupings:
                continue
            props = " ".join(sorted(short(p) for p in proposals.get(node, ())))
            if any(d[0] == node for d in disagreements):
                status = "disagreement"
            elif node not in typed and (children[node] or props):
                status = "untyped"
            else:
                continue
            queue.append((status != "disagreement", -children[node], -parents[node], status, node, props))
        for _, nc, np, status, node, props in sorted(queue):
            w.writerow([status, titles[node], short(node), files.get(node, ""), -nc, -np, props])
    print(f"  {len(queue):,} nodes queued for curation -> {out.relative_to(ONT.parent)}")
    top = [q for q in sorted(queue) if q[3] == "untyped"][:10]
    print("  top untyped nodes by sub-ecosystem count: "
          + ", ".join(f"{titles[q[4]]} ({-q[1]})" for q in top))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--rebuild", action="store_true", help="regenerate build/ecosystems.nt first")
    ap.add_argument("--skip-reasoner", action="store_true", help="skip the HermiT step")
    ap.add_argument("--skip-shacl", action="store_true", help="skip the SHACL step")
    args = ap.parse_args()
    sys.path.insert(0, str(ONT / "scripts"))

    modules = parse_modules()
    if failures:
        return 1
    vocabulary_hygiene(modules)
    store = load_store(args.rebuild)
    overlay_integrity(store)
    derived = run_rules(store)
    if not args.skip_reasoner:
        reason(derived)
    if not args.skip_shacl:
        shacl(store)
    coverage(store, derived)

    section("Result")
    if failures:
        print(f"  {len(failures)} problem(s)")
        return 1
    print("  all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
