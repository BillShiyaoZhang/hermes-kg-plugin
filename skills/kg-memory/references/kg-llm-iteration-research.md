> Historical design/research note. It does not describe the current installed runtime.
> For executable paths, schema, configuration and migration rules use [the active skill](../SKILL.md)
> and [current storage contract](entity-schema.md). Do not execute old cleanup or deployment examples.

# KG LLM Iteration — Academic Reference Bank

This file stores the condensed research findings from the iteration planning session (May 2026). See `~/.hermes/plans/kg-llm-iteration-proposal.md` for the full proposal document (v0.5).

---

## Related Authors' Papers (Priority References — v0.5)

### Shiyao Zhang (User — self)

| Paper | Venue | Relevance | Status |
|-------|-------|-----------|--------|
| LLM Multi-Agent Dialogue for Ontology Alignment | AAMAS 2024 | Phase 3 core — directly applicable to Hermes-OpenClaw alignment | arXiv ID 暂定 |
| LLM-Assisted Ontology-Based Task-Oriented Data Exchange | BDDM 2025 | agent-comm interop; KG as medium for agent communication | arXiv ID 暂定 |

### Terry Payne (University of Liverpool)

| Paper | Venue | Relevance | arXiv ID |
|-------|-------|-----------|----------|
| Discoverable Agent Knowledge | arXiv 2026 | Phase 3 core — agentic KG affordances | 2605.19186 |
| OwlLM: Large Language Models for OWL Ontologies | ISWC 2024 | Phase 3 — LLM→OWL reasoning | — |

### Valentina Tamma (University of Liverpool)

| Paper | Venue | Relevance | Notes |
|-------|-------|-----------|-------|
| LLM-Generated Competency Questions: Cross-Domain Empirical Study | arXiv 2026 | Phase 3 direct — CQ generation for ontology evaluation | with Payne, Alharbi, de Berardinis |
| RELRaE: LLM-Based Relationship Extraction, Labelling, Refinement and Evaluation | arXiv 2025 | Phase 1/3 direct — extract-refine-evaluate pipeline | with Payne, Hannah, Mitchell |
| Task-Based Ontology Evaluation for Question Generation | Frontiers 2025 | Phase 3 reference | with Payne, Grasso, AlKhuzaey |
| Competency Question Retrofitting with Open-Source LLMs | AAAI Symp. 2024 | Phase 3 reference — open LLM baseline | with Payne, Grasso, Alharbi |

### Kerry Taylor + Zhangcheng Qiang (CSIRO / University of Queensland)

| Paper | Venue | Relevance | Notes |
|-------|-------|-----------|-------|
| Agent-OM: Leveraging LLM Agents for Ontology Matching | VLDB 2024 | Phase 3 direct — first LLM-agent approach to OM | Qiang + Wang + Taylor |
| OM4OV: Leveraging Ontology Matching for Ontology Versioning | VLDB 2024 | Phase 3 extension — OM applied to versioning | Qiang + Taylor |
| How Does A Text Preprocessing Pipeline Affect Ontology Syntactic Matching? | VLDB 2024 | Phase 3 reference — preprocessing for OM | Qiang + Taylor |

> **Attribution note**: Zhangcheng Qiang (CSIRO/Queensland) is distinct from other researchers named "Qiang Zhang" in the ML community. Always verify via OpenAlex author ID before citing.

---

## Taxonomy of Methods (from literature review)

### Session / Dialogue Extraction
- Phase 1 is novel territory; limited directly relevant work
- **RELRaE** (Tamma+Payne, 2025) provides extract-refine-evaluate loop applicable to session extraction

### NL → Query (Cypher / SPARQL)
- **SPARQL-LLM** (arXiv 2025) — NL → SPARQL via structured LLM prompting
- **ARUQULA** (arXiv 2025) — adaptive retrieval for KGQA
- **AGENTICT²S** (arXiv 2025) — KG-enhanced scientific tool use

### Ontology / Entity Alignment
- **Multi-Agent Dialogue for OA** (Shiyao Zhang, AAMAS 2024) — LLM multi-agent negotiation for ontology alignment
- **Discoverable Agent Knowledge** (Payne et al., 2026) — agentic KG affordances
- **OwlLM** (Payne et al., ISWC 2024) — LLM reasoning over OWL ontologies
- **Agent-OM** (Qiang+Taylor, VLDB 2024) — LLM agents for ontology matching
- **Competency Question Retrofitting** (Tamma+Payne) — NL query → ontology schema alignment

---

## Key Design Decisions (from proposal discussion)

### Phase 1: Session Extraction
- **Current**: Rule-based extractor in `session.py` (person names, event types, preferences, decisions)
- **Target**: LLM-powered extraction with deeper relation inference
- **Open question**: sync (per-turn) vs async (queue + cron)
- **Risk**: LLM cost per turn; mitigations: batch + cron, or keep rule-based for simple cases

### Phase 2: NL → Cypher
- **Current**: `query_natural.py` rule engine — fixed patterns (上个月/上周/今天)
- **Target**: LLM maps NL to Cypher with schema grounding
- **Architecture**: Dual-track — Fast rule (<50ms) for simple cases + Deep LLM for complex
- **Open question**: MiniMax → Cypher stability; need benchmark first
- **Reference**: SPARQL-LLM for prompting strategy

### Phase 3: Ontology Alignment
- **Current**: Hermes (`urn:hermes:kg:*`) and OpenClaw (`urn:openclaw:kg:*`) share Neo4j but isolated
- **Target**: Cross-KG alignment using Shiyao Zhang's AAMAS 2024 work + Discoverable Agent Knowledge framework + Agent-OM
- **Prerequisite**: Confirm OpenClaw team schema; establish alignment ontology
- **Key papers**: OwlLM (OWL reasoning), Agent-OM (LLM-agent OM), Competency Question methods

### Cross-Cutting
- **No API keys in scripts**: User rejected standalone LLM API key approach; use Hermes's built-in LLM
- **LLM reasoning (agent-side) + Neo4j write (script-side)**: Split architecture confirmed with user
- **Phase ordering**: 1 → 2 → 3 (extraction → query → alignment); WoT deprioritized

---

## Verification Checklist (for future sessions)

When resuming work on this proposal:
- [ ] Confirm arXiv IDs for Shiyao Zhang AAMAS 2024 and BDDM 2025 papers
- [ ] Verify Valentina Tamma arXiv 2026 Competency Questions paper arXiv ID (not yet retrieved)
- [ ] Review `~/.hermes/plans/kg-llm-iteration-proposal.md` for latest open discussion points
- [ ] Check the configured Feishu target for sending updates (chat ID lives in the local profile config, not in this repo)
- [ ] **Before citing any paper, verify author identity via OpenAlex author ID** — common Chinese names (e.g., "Qiang Zhang") return multiple unrelated researchers
