# Crypto Ecosystems Ontology (CEO)

An OWL 2 / RDFS class and property hierarchy that sits on top of the taxonomy in
`data/ecosystems/`. The taxonomy records one relation, "X is a sub-ecosystem of
Y", which in practice stands for at least seven different things: an L2 settling on
an L1, a dapp deployed on a chain, a chain in a VM family, a project in a category,
a company building on a chain, a tool for a platform, a chain launched from a token.
This ontology says what each node **is** and what each link **means**, without
changing a single TOML file.

```
ontology/
  ceo.ttl                 core OWL ontology: classes, properties, axioms
  vocab/categories.ttl    SKOS: functional categories (DeFi > DEX, Infrastructure > Oracle, ...)
  vocab/repo-roles.ttl    SKOS: repository roles; absorbs the 127 raw tag strings
  vocab/technologies.ttl  reference individuals: EVM, SVM, Move VM, Cosmos SDK, OP Stack, ...
  overlay/groupings.ttl   curated: what each grouping node groups by
  overlay/entities.ttl    curated: types and typed links for ~130 well-known nodes (seed)
  rules/*.rq              SPARQL CONSTRUCT rules: raw links + node types -> typed links
  shapes.ttl              SHACL: closed-world checks OWL cannot express
  queries/*.rq            example queries
  scripts/toml_to_rdf.py  TOML -> RDF (taxonomy layer)
  scripts/check.py        parse, lint, derive, reason (HermiT), validate (SHACL), report
  scripts/query.py        run a query over everything
```

## Quick start

```bash
pip install -r ontology/requirements.txt     # rdflib, pyshacl, pyoxigraph, owlready2; Java for HermiT
make ontology-rdf                            # data/ecosystems -> ontology/build/*.nt
make ontology-check                          # ~15s; writes build/derived.ttl, build/curation-queue.tsv
python3 ontology/scripts/query.py ontology/queries/q04-evm-networks.rq
python3 ontology/scripts/query.py ontology/queries/q02-aggregation-policies.rq --full   # ~1 min
```

`ontology/build/` is generated and git-ignored. `ecosystems.nt` is the ecosystem
graph (7,190 nodes, 55k triples). `ecosystems-full.nt.gz` adds all 438,379
repositories (2.2M triples).

## Design

### Three layers, one IRI per node

| Layer | Who writes it | What it holds |
|---|---|---|
| **Taxonomy** | `toml_to_rdf.py`, mechanically | `ceo:Ecosystem`, `ceo:Repository`, `ceo:CodeHostAccount`, `ceo:hasSubEcosystem`, `ceo:hasRepository`, raw tags. Lossless, no judgement. |
| **Domain** | curators (`overlay/`) and rules (`rules/`) | What a node denotes (`ceo:Layer1`, `ceo:Protocol`, `ceo:Wallet`, ...) and typed relations (`ceo:settlesOn`, `ceo:deployedOn`, ...). |
| **Facets** | curators (`vocab/`) | SKOS concept schemes for functional categories and repository roles. |

A taxonomy node keeps the IRI `eco:<file-slug>` (e.g. `eco:ethereum` for
`data/ecosystems/e/ethereum.toml`) in every layer. The converter types it
`ceo:Ecosystem`; the overlay adds a second type such as `ceo:Layer1`. Slugs come
from titles, so the overlay survives a move to upstream's newer export format as
long as titles are stable.

### Why OWL classes for some things and SKOS for others

**Classes** are for *kinds* of things that take different relations and
constraints. A `ceo:Layer2` settles on something; a `ceo:Token` is issued on
something; a `ceo:Service` is operated by someone. These are disjoint where that
is true, so a reasoner catches contradictions such as one node typed both
`ceo:Layer1` and `ceo:Layer2`, or a protocol `ceo:deployedOn` a network family.

**SKOS concepts** are for *functional categories* (oracle, DEX, data
availability, gaming). These cut across kinds: Chainlink is a protocol and Band is
a chain, but both are oracles. They also change faster than kinds. Making
them classes would give 50 overlapping classes and constant multiple inheritance.
The few categories that drive relations are bridged with defined classes:
`ceo:DataAvailabilityProvider` is "any entity with `hasCategory cat:DataAvailability`".

### Class hierarchy

```
ceo:Ecosystem                         taxonomy node (every TOML file)
  ceo:EcosystemGrouping               a node that only groups others            ⊥ CryptoEntity
    ceo:CategoryGrouping              "Wallet (Category)", "Oracles (Category)"
    ceo:TechnologyGrouping            "Move Stack", "EVM Compatible Layer 2s", "Ethereum L2s"
    ceo:CatchAllGrouping              "General", "Multichain Infrastructure"
ceo:Repository
ceo:CodeHostAccount

ceo:CryptoEntity                      what a node denotes; the 8 branches are pairwise disjoint
  ceo:Network
    ceo:Layer1 ⊥ ceo:Layer2 ⊥ ceo:Sidechain ⊥ ceo:PeerToPeerNetwork
    ceo:Layer2                        ⊑ ∃settlesOn.Network
      ceo:Rollup
        ceo:OptimisticRollup ⊥ ceo:ZKRollup
      ceo:Validium, ceo:Optimium, ceo:StateChannelNetwork
      ceo:Layer3                      ≡ Layer2 ⊓ ∃settlesOn.Layer2      (defined)
    ceo:Appchain, ceo:Testnet         facets, combine with the above
  ceo:NetworkFamily                   Cosmos Network, Polkadot Network, Polygon
  ceo:Protocol                        on-chain contracts/modules: Uniswap, Chainlink, EigenLayer
  ceo:Software
    ceo:Application > ceo:Wallet, ceo:Explorer
    ceo:DeveloperTool, ceo:Library > ceo:SDK, ceo:NodeClient
    ceo:ChainFramework > ceo:RollupFramework, ceo:ConsensusEngine
  ceo:Specification
    ceo:ExecutionEnvironment ⊥ ceo:ProgrammingLanguage ⊥ ceo:Standard
  ceo:Service > ceo:CentralizedExchange
  ceo:Organization > ceo:Company, ceo:Foundation, ceo:DAO, ceo:Community
  ceo:Asset > ceo:Token > ceo:Stablecoin
    ceo:NativeAsset                   ≡ Asset ⊓ ∃nativeAssetOf.Network  (defined)

Defined role classes (inferred, never asserted):
  ceo:EVMCompatibleNetwork ≡ Network ⊓ supportsExecutionEnvironment value tech:evm
  ceo:SVMNetwork, ceo:MoveNetwork      (same pattern)
  ceo:DataAvailabilityProvider, ceo:OracleProvider   (by category)
```

The defined `*Network` classes replace the taxonomy's hand-maintained groupings:
"EVM Compatible Layer 1s" is the query `EVMCompatibleNetwork ⊓ Layer1`.

### Property hierarchy

```
ceo:rollsUpInto  (transitive)             "counts toward", the source taxonomy's semantics
  ceo:subEcosystemOf                        the raw link (inverse of ceo:hasSubEcosystem)
  ceo:buildsOn                              generic typed reading of a raw link
    ceo:deployedOn  > ceo:homeNetwork, ceo:issuedOn, ceo:connectsNetwork
    ceo:supportsNetwork > ceo:clientOf
  ceo:securedBy > ceo:settlesOn
  ceo:sidechainOf, ceo:memberOfFamily, ceo:testnetOf, ceo:partOf

ceo:architecturalRelation
  ceo:securedBy, ceo:sidechainOf, ceo:usesDataAvailability, ceo:hasNativeAsset / nativeAssetOf
  ceo:usesTechnology > ceo:supportsExecutionEnvironment, ceo:builtWith,
                       ceo:implementsStandard, ceo:supportsLanguage
  ceo:compilesTo
ceo:lineageRelation > ceo:derivedFrom (transitive) > ceo:forkOf, ceo:successorOf
ceo:organizationalRelation > ceo:developedBy / develops, ceo:operatedBy,
                             ceo:governedBy / governs, ceo:stewardedBy
ceo:hasCategory > ceo:primaryCategory                 (to SKOS concepts)
ceo:taxonomyRelation > hasSubEcosystem, hasRepository, hasCodeHostAccount,
                       ownedByAccount, hasRole
```

Two choices matter most:

1. **`ceo:rollsUpInto` is the bridge.** The raw `subEcosystemOf` and every typed
   relation the taxonomy would have written as a sub-ecosystem link are
   sub-properties of it. Aggregation queries therefore work the same over raw data,
   curated data or both. A narrower counting policy is just a query over a subset
   of the sub-properties (see `q02`).
2. **`ceo:supportsLanguage` is a property chain**, `supportsExecutionEnvironment ∘
   compilesTo⁻`. Assert that Solidity compiles to the EVM once, and every EVM
   network supports Solidity. That only holds if execution environments are
   specific enough, which is why ink!'s Substrate contracts runtime is its own
   individual rather than generic Wasm. The reasoner surfaced exactly that bug
   during development: NEAR was inferred to run ink!.

### From raw links to typed links

Curators type **nodes**; rules type **links**. Each rule in `rules/` is one
CONSTRUCT that fires on a raw `hasSubEcosystem` link once its endpoints are typed:

| Rule | When | Derives |
|---|---|---|
| G1–G3 | parent is a grouping with a category / environment / technology criterion | `hasCategory`, `supportsExecutionEnvironment`, `usesTechnology` |
| G4, G6 | grouping criterion is a class, or a property + anchor, and the member lacks the type | `ceo:proposedType` (an annotation for review, not a type) |
| G5 | relational grouping such as "Ethereum L2s" (criterion `settlesOn`, anchor Ethereum) | `settlesOn eco:ethereum` for typed members |
| E01 | Network > Layer2 without a settlement layer (not under a DA layer) | `settlesOn` |
| E02 | NetworkFamily > Network | `memberOfFamily` |
| E03 / E04 / E05 | Network > Protocol / Token / Software or Service | `deployedOn` / `issuedOn` / `supportsNetwork` |
| E06 | Network > Organization; Family or Protocol > non-network | `buildsOn` |
| E07 | ChainFramework or ConsensusEngine > anything | `builtWith` |
| E08 / E09 / E10 | Asset > Network; Network > Sidechain; DA layer > Layer2 | `hasNativeAsset` / `sidechainOf` / `usesDataAvailability` |
| E11 | Network, Family or Protocol > *untyped* node | `buildsOn` (least-committal fallback) |
| D1 / D2 | `evmChainId`; titles like "Irys (prev Bundlr)" | `caip2 "eip155:<id>"`; `formerName` |

Rules run in one pass over taxonomy + overlay and write `build/derived.ttl`. Their
output is checked by the reasoner and by SHACL exactly like curated data. If two
rules give an L2 two settlement layers, SHACL reports it.

### Modelling rules for curators

1. Type a node by what it denotes **today**. Record history with `skos:historyNote`
   (Celo was an L1 until March 2025; Helium moved to Solana in 2023).
2. If a node bundles an organization and its product, type it as the product and
   link the organization with `ceo:developedBy` when that has its own node.
3. If a chain brand covers several networks, type it by its flagship network
   (Arbitrum → Arbitrum One). Use `ceo:NetworkFamily` when it is mainly an umbrella
   over networks typed individually (Cosmos, Polkadot, Polygon).
4. Do not restate what a rule derives. Type the node and let the raw link do the rest.
5. A raw link that is wrong or historical gets no typed equivalent; explain it with
   `skos:editorialNote` (Ethereum > Aeternity).
6. Link out instead of re-modelling other datasets: `ceo:caip2`, `ceo:evmChainId`,
   `ceo:l2beatSlug`, `ceo:defillamaSlug`, `ceo:coingeckoId`, `ceo:wikidataItem`.

## Current state (seed overlay)

`make ontology-check` on this snapshot:

* **Consistent** under HermiT (OWL 2 DL), with 0 SHACL violations and 0 warnings.
  Negative tests are rejected as expected: Polygon as a `Layer1`, Arbitrum as a
  `Layer1`, a protocol deployed on a family, two home networks, a malformed CAIP-2.
* **Typed:** 127 entities and 25 groupings of 7,190 nodes.
* **Raw links (7,102):** 101 have a specific typed relation, 474 are explained by a
  grouping criterion, 5,450 get the generic `buildsOn` fallback, 168 sit in
  catch-all groupings, and 909 remain uninterpreted (mostly children of untyped
  chains such as Flow, Aurora, Ergo, Harmony).
* **Inferred:** `ceo:Layer3` = {XAI, Sanko}; 33 EVM-compatible networks; Solidity,
  Vyper and Yul on 235 EVM-supporting entities via the property chain.
* **Tags:** 5,336 of 5,374 tag occurrences (99.3%) resolve to a role or category.
  The 23 unresolved ones are product names such as "Flatqube" and "CosmosKit",
  kept as `ceo:rawTag`.
* **Curation queue:** 240 nodes in `build/curation-queue.tsv`, ordered by how many
  links typing them would unlock. It includes 3 disagreements between curated types
  and grouping proposals; for example, Gnosis is in "EVM Compatible Layer 2s" but
  it is the DAO, and the chain is "Gnosis Chain".

Two findings the raw tree cannot show (`q02`, explicit `[[repo]]` entries only;
repos of listed GitHub orgs are not enumerated in the TOML):

| Policy for "the Ethereum ecosystem" | Ecosystems | Repos |
|---|---:|---:|
| raw sub-ecosystem tree | 1,640 | 126,078 |
| any typed rollup relation | 1,629 | 132,278 |
| settlement + deployment only | 32 | 87,728 |
| settlement only (Ethereum, its L2s and L3s) | 19 | 82,587 |

* **Starknet and Celo both settle on Ethereum, but the raw tree does not count them
  toward it.** Their only raw parents are the "Ethereum L2s" / "EVM Compatible
  Layer 2s" groupings, which sit outside Ethereum's subtree. The typed layer
  recovers their ~10.8k repos.
* Most of the raw tree's breadth comes from the generic "listed under" link. So
  far only 31 nodes (plus Ethereum itself) are typed as settling on or deploying to
  Ethereum.

## Limitations and next steps

* **Namespace.** `https://w3id.org/cryptoeco/` is a placeholder. Register it (a
  pull request to `perma-id/w3id.org`) or pass `--base` to the converter and
  replace the prefix in the `.ttl` files.
* **Coverage.** Typing the top ~250 nodes in the curation queue would interpret most
  of the remaining links. Leaves are best typed in bulk from their repos' roles and
  from the grouping proposals.
* **Time.** Facts describe the present and history goes in notes. If you need
  "as of" queries, add temporal qualification (e.g. reified links with
  `prov:startedAtTime`), or follow upstream's dated migrations.
* **Upstream.** Upstream (now `electric-capital/open-dev-data`) has moved to a
  migration DSL with JSONL exports. The overlay is keyed by title slug, so a JSONL
  reader for `toml_to_rdf.py` is the only change needed to put this ontology over
  current upstream data. The `#developer-tool`-style tags already resolve.
* **Provenance.** Curated assertions are attributed through git history only.
  Named graphs (raw / curated / derived) or PROV-O can be added if the data is
  loaded into a shared triple store.
