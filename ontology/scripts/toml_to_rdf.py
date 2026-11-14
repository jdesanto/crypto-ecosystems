#!/usr/bin/env python3
"""Lift data/ecosystems/**/*.toml into RDF using the Crypto Ecosystems Ontology.

This produces the *taxonomy layer* only: a lossless, judgement-free translation
of the TOML files. Typing lives in ontology/overlay/ and ontology/rules/.

  python3 ontology/scripts/toml_to_rdf.py                      # full graph, ~3M triples
  python3 ontology/scripts/toml_to_rdf.py --no-repos           # ecosystem graph only
  python3 ontology/scripts/toml_to_rdf.py --out build/x.nt.gz  # gzip by extension

Output is streamed N-Triples, so the full export needs no in-memory graph.
Requires rdflib only to read the SKOS vocabularies used for tag resolution.
"""

from __future__ import annotations

import argparse
import collections
import gzip
import re
import sys
import tomllib
from pathlib import Path
from urllib.parse import quote, urlsplit

ONTOLOGY_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = ONTOLOGY_DIR.parent
DEFAULT_BASE = "https://w3id.org/cryptoeco/"

RDF_TYPE = "http://www.w3.org/1999/02/22-rdf-syntax-ns#type"
RDFS_LABEL = "http://www.w3.org/2000/01/rdf-schema#label"
XSD = "http://www.w3.org/2001/XMLSchema#"
SKOS = "http://www.w3.org/2004/02/skos/core#"


def normalize_label(text: str) -> str:
    """Case-fold and treat '#', '-', '_' as spaces so '#developer-tool' == 'Developer tool'."""
    text = text.strip().lstrip("#").lower()
    text = re.sub(r"[-_]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def iri_escape(iri: str) -> str:
    """Percent-encode characters that are illegal inside an N-Triples IRIREF."""
    return quote(iri, safe=":/?#[]@!$&'()*+,;=%~.-_")


def literal(value: str, datatype: str | None = None) -> str:
    escaped = (
        value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n").replace("\r", "\\r")
    )
    return f'"{escaped}"^^<{datatype}>' if datatype else f'"{escaped}"'


def normalize_account_url(url: str) -> str:
    return url.strip().rstrip("/").lower()


def account_of_repo(url: str) -> str | None:
    """github.com/<owner>/<repo> -> github.com/<owner> (lower-cased); None for other shapes."""
    parts = urlsplit(url)
    segments = [s for s in parts.path.split("/") if s]
    if parts.netloc.lower() in ("github.com", "www.github.com") and len(segments) >= 2:
        return f"https://github.com/{segments[0]}".lower()
    return None


class TagResolver:
    """Resolve raw tag strings to SKOS concepts using the labels in the vocab files."""

    def __init__(self, vocab_files: list[Path]):
        import rdflib  # local import: only needed here

        graph = rdflib.Graph()
        for path in vocab_files:
            graph.parse(path, format="turtle")
        skos = rdflib.Namespace(SKOS)
        self.lookup: dict[str, set[tuple[str, str]]] = collections.defaultdict(set)
        for concept, scheme in graph.subject_objects(skos.inScheme):
            for prop in (skos.prefLabel, skos.altLabel, skos.hiddenLabel):
                for label in graph.objects(concept, prop):
                    self.lookup[normalize_label(str(label))].add((str(scheme), str(concept)))

    def resolve(self, tag: str) -> set[tuple[str, str]]:
        return self.lookup.get(normalize_label(tag), set())


class Writer:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.fh = gzip.open(path, "wt", encoding="utf-8") if path.suffix == ".gz" else path.open("w", encoding="utf-8")
        self.count = 0

    def triple(self, s: str, p: str, o: str, *, obj_is_literal: bool = False) -> None:
        obj = o if obj_is_literal else f"<{iri_escape(o)}>"
        self.fh.write(f"<{iri_escape(s)}> <{p}> {obj} .\n")
        self.count += 1

    def close(self) -> None:
        self.fh.close()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data", default=str(REPO_ROOT / "data" / "ecosystems"), help="ecosystems directory")
    ap.add_argument("--out", default=None, help="output .nt or .nt.gz (default: ontology/build/...)")
    ap.add_argument("--no-repos", action="store_true", help="omit [[repo]] entries (ecosystem graph only)")
    ap.add_argument("--base", default=DEFAULT_BASE, help=f"IRI base (default {DEFAULT_BASE})")
    args = ap.parse_args()

    base = args.base.rstrip("/") + "/"
    ceo = base + "ontology#"
    eco_ns = base + "id/ecosystem/"
    role_scheme = base + "vocab/repo-role/scheme"
    data_dir = Path(args.data)
    out = Path(args.out) if args.out else ONTOLOGY_DIR / "build" / (
        "ecosystems.nt" if args.no_repos else "ecosystems-full.nt.gz"
    )

    resolver = TagResolver([ONTOLOGY_DIR / "vocab" / "repo-roles.ttl", ONTOLOGY_DIR / "vocab" / "categories.ttl"])
    if base != DEFAULT_BASE:  # vocab files use the default base; rebase resolved concepts
        resolver.lookup = {
            k: {(s.replace(DEFAULT_BASE, base), c.replace(DEFAULT_BASE, base)) for s, c in v}
            for k, v in resolver.lookup.items()
        }

    # Pass 1: titles -> IRIs (slug = lower-cased file stem, the canonical path the Rust tool writes).
    files = sorted(data_dir.glob("**/*.toml"))
    ecosystems: list[tuple[Path, dict]] = []
    title_to_iri: dict[str, str] = {}
    for path in files:
        with path.open("rb") as fh:
            eco = tomllib.load(fh)
        iri = eco_ns + path.stem.lower()
        if iri in title_to_iri.values():
            print(f"error: slug collision for {path}", file=sys.stderr)
            return 1
        title_to_iri[eco["title"]] = iri
        ecosystems.append((path, eco))

    w = Writer(out)
    seen_accounts: set[str] = set()
    seen_repos: set[str] = set()
    tag_hits: collections.Counter[str] = collections.Counter()
    tag_misses: collections.Counter[str] = collections.Counter()

    def account(url_original: str) -> str:
        iri = normalize_account_url(url_original)
        if iri not in seen_accounts:
            seen_accounts.add(iri)
            w.triple(iri, RDF_TYPE, ceo + "CodeHostAccount")
            w.triple(iri, ceo + "url", literal(url_original.strip(), XSD + "anyURI"), obj_is_literal=True)
        return iri

    for path, eco in ecosystems:
        s = title_to_iri[eco["title"]]
        w.triple(s, RDF_TYPE, ceo + "Ecosystem")
        w.triple(s, RDFS_LABEL, literal(eco["title"]), obj_is_literal=True)
        w.triple(s, ceo + "sourceFile", literal(str(path.relative_to(REPO_ROOT)) if path.is_relative_to(REPO_ROOT) else str(path)), obj_is_literal=True)

        for child_title in eco.get("sub_ecosystems") or []:
            child = title_to_iri.get(child_title)
            if child is None:
                print(f"warning: {eco['title']!r} lists unknown sub-ecosystem {child_title!r}", file=sys.stderr)
                continue
            w.triple(s, ceo + "hasSubEcosystem", child)
            w.triple(child, ceo + "subEcosystemOf", s)

        for org in eco.get("github_organizations") or []:
            w.triple(s, ceo + "hasCodeHostAccount", account(org))

        if args.no_repos:
            continue
        for repo in eco.get("repo") or []:
            url = repo["url"].strip()
            r = url.lower()
            w.triple(s, ceo + "hasRepository", r)
            if r not in seen_repos:
                seen_repos.add(r)
                w.triple(r, RDF_TYPE, ceo + "Repository")
                w.triple(r, ceo + "url", literal(url, XSD + "anyURI"), obj_is_literal=True)
                owner = account_of_repo(url)
                if owner:
                    w.triple(r, ceo + "ownedByAccount", account(owner))
            if repo.get("missing"):
                w.triple(r, ceo + "markedMissing", literal("true", XSD + "boolean"), obj_is_literal=True)
            for tag in repo.get("tags") or []:
                w.triple(r, ceo + "rawTag", literal(tag), obj_is_literal=True)
                hits = resolver.resolve(tag)
                (tag_hits if hits else tag_misses)[tag] += 1
                for scheme, concept in hits:
                    prop = "hasRole" if scheme == role_scheme else "hasCategory"
                    w.triple(r, ceo + prop, concept)

    w.close()
    print(f"wrote {w.count:,} triples to {out}")
    print(f"  ecosystems: {len(ecosystems):,}  code-host accounts: {len(seen_accounts):,}  repositories: {len(seen_repos):,}")
    if not args.no_repos:
        total = sum(tag_hits.values()) + sum(tag_misses.values())
        print(f"  tag occurrences resolved: {sum(tag_hits.values()):,}/{total:,} "
              f"({len(tag_hits)} of {len(tag_hits) + len(tag_misses)} distinct tags)")
        if tag_misses:
            print("  unresolved tags (kept as ceo:rawTag): " + ", ".join(f"{t} ({n})" for t, n in tag_misses.most_common()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
