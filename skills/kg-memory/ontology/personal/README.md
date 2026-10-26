# Personal application ontology

`profile.ttl` is the human-maintained application profile. It selects public
terms, explains the remaining local terms, supplies Chinese presentation text,
and binds them to existing storage keys. The public vocabulary releases are
unmodified publisher downloads; their versions, formats, SHA-256 checksums and
license notices are recorded in `sources.lock.json` and `NOTICE.md`.

The build creates three files. Do not edit these generated artifacts:

* `manifest.json`: namespace-overlay operations, source/semantic bindings,
  provenance terms and a derived core runtime contract.
* `shapes.ttl`: SHACL for the supported application constraints and the existing
  required fields, including the explicit unknown-time Event exception.
* `selected.ttl`: the selected declarations and bounded inheritance/disjointness
  graph used for offline checks. It is not a merged copy of every upstream rule.

From the repository root:

```powershell
python scripts/build_personal_ontology.py
python scripts/build_personal_ontology.py --check
$env:PYTHONPATH = 'plugins'
python -m unittest kg.tests.test_ontology_profile
```

The compiler uses only local RDF/Turtle or RDF/XML, rejects network imports and
unsupported profile expressions, verifies source checksums, and checks selected
domain/range restrictions. Schema.org candidate lists are alternatives;
multiple RDFS domain/range assertions remain conjunctive. Local subproperties
must also satisfy their public parents. The check command does not write files.
Actual SHACL data validation uses the development `pyshacl` dependency, never
the normal agent/write path.

An offline projection contains both the published semantic type and a temporary
`urn:hermes:personal:runtime-type:<key>` marker. Shapes apply application limits
to these markers rather than globally redefining public vocabulary properties.
For example, a memory record can describe a real Event with `schema:about`,
while the application's `X_PERSONAL_ABOUT` edge is restricted to information
records about Topics. Its marker scope also keeps a HealthRecord from gaining
all fields permitted for the separate Resource storage type merely because
both are CreativeWorks. The projection and all validation inferences are
temporary and do not add facts, labels or inverse edges to Neo4j.

Existing Person/Event/Location identities remain unchanged. Legacy Concept is
not equated to SKOS Concept; the profile introduces a dedicated Topic type.
Likewise, a personal Project is a task collection, not Schema.org's
Organization-derived Project. The old Person/Event `HERMES_locatedAt` edge has
only an annotation reference to `schema:location`: the locked 30.1 release
does not include Person in that property's candidate domains. Annotation
references are never turned into public assertions by the projection.

For upstream maintenance, replace a source only with a verified publisher
release, update its version and checksum, review changed selected terms, and
increment the application profile version. Rebuild and run the ontology and
runtime compatibility tests before release. Existing workspaces receive no
automatic semantic upgrade; use the explicit preset installation command.
The package always remains usable without publisher/network availability.
