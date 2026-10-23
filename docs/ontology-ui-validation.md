# Ontology Explorer verification

Verified on 2026-10-23 with native Windows Python, Neo4j 5.26 Community and the
production frontend build. All mutating browser acceptance fixtures were
synthetic and isolated from the personal profile. Design intent and future
requirements are in [the UI/UX design](product-ui-ux-design.md).

## Implemented scope

- A fifth main navigation destination, **知识结构**, with a bookmarkable
  `?view=ontology` URL, browser back/forward and page focus management.
- Six entity definitions, nine relationship definitions and local Turtle
  property definitions parsed with RDFLib. Effective runtime endpoint rules,
  compatibility aliases, enum values and conditional requirements are explicit.
  Differences between the stored ontology and runtime are reported rather than
  silently rewritten. This is a viewer, not an ontology editor or OWL reasoner.
- A directional type diagram with an equivalent keyboard-accessible relationship
  table. The diagram represents allowed types, not an instance graph or measured
  relationship strength. Universal relationships do not introduce fake entities.
- Active instance and relationship browsing in the current namespace, keyword
  filtering and server pagination. Both relationship endpoints must be in scope;
  quarantined, disabled, legacy and other namespaces' records are excluded.
- Existing memory details, source evidence, revision history and correction flows
  remain reachable from instance names and relationship endpoints.
- Shared typography, focus, touch-target and layout foundations, six-type memory
  library filters, neutral product branding and grouped navigation.

## Verification results

| Check | Result |
| --- | --- |
| Python regression, real test database and cached E5 enabled | 293 passed; zero skips |
| Frontend unit tests | 22 passed |
| TypeScript and Vite production build | Passed |
| Definition/API contracts | RDF parsing, packaged resource location, runtime differences, unknown vs zero, bounds, aliases and injection handling passed |
| Visibility with real Cypher | Current namespace and active records enforced; shared/private foreign endpoints, archived and legacy records excluded |
| Normal browser flow | Authentication, type → instances, relation filtering, endpoint details, empty results and clear-filter recovery passed |
| Pagination | Real 26-record type traversed; final page correct; focus returns to the result region after page changes |
| Keyboard | View direction keys, browse-to-data focus, modal Tab/Shift+Tab wrap, Escape restoration and Ctrl/⌘K modal isolation passed |
| Reflow | Structure checked at 320, 390, 768 and 1280 CSS pixels; data checked at 320 and desktop width. No whole-page horizontal overflow in those states; wide diagrams/tables scroll locally |
| Failure state | Synthetic unavailable store still shows definitions, unknown counts as “—” and explicit instance-load error; invalid token rejected |
| Release package | Clean wheel includes the ontology module, local Turtle and latest JS/CSS; 26 Python modules and 21 skill resources match source. Repository-external isolated import, schema, HTML, static assets and authenticated API smoke passed |

The viewport checks used a private same-origin iframe harness because the
available in-app browser viewport override did not produce reliable narrow
viewport captures. Only that synthetic harness permits same-origin framing;
the shipped console retains `frame-ancestors 'none'`. These checks establish CSS
reflow, not physical mobile-device or touch-screen acceptance.

Browser acceptance found and fixed an SVG selector that also resized the title
icon, lost focus during page loading, a visually hidden table heading causing
narrow-page overflow, mobile navigation inheriting a column layout, and a global
search shortcut reaching behind a modal. Authenticated reads use GET and do not
send ontology or instance data to an external model or rendering service.

Raw logs, fixtures and screenshots remain in Git-ignored local reports. They do
not contain the owner's historical conversations. Production installation uses
its existing profile, credentials and runtime settings; no local paths or
connection secrets are embedded in the shipped feature.

## Local deployment

The updated console is installed at the existing local service. Its bookmark is
`http://127.0.0.1:8765/?view=ontology` and it uses the existing access token.
Authenticated, read-only acceptance returned six entity definitions, nine
relationship definitions, 164 active entities and 83 active internal
relationships in the current namespace at the time of verification. The real E5
runtime remained ready and the existing synchronization settings were preserved.

The previous installed UI was backed up before replacement. Hash comparisons
confirmed that profile configuration, credentials, typed memory entities and
their internal relationships were unchanged. Unauthenticated API requests still
returned 401. Private deployment scripts, backup manifests and actual profile
connection details remain in ignored local reports, outside release resources.

The release audit found no local profile, personal data, reports, caches, known
credentials or machine-specific paths in the wheel. The release package and
private audit evidence are Git-ignored; this work did not commit or upload them.

## Remaining verification

This run does not certify WCAG conformance. NVDA/VoiceOver/Safari, physical mobile
devices, high-contrast mode, text-only 200% scaling, browser 400% zoom and the
planned user study still require dedicated acceptance. Large-library performance
and broader host compatibility remain separate release work. The design document
labels these goals and planned capabilities separately from this implementation.
