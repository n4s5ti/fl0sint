# Toolspace Cartographer Pipeline — Brief for Web Oracle

## What this is
An OSINT tool discovery and pivot validation pipeline built on an Obsidian-backed toolshed catalog (1318 tools) with a self-referential validation loop (dogf00dd) and meta-analysis (coverage heatmap, gap detection, micro-module extraction).

## Architecture

```
OSINT Framework (arf.json + Full Tagged Tree.md)  ──┐
FlowSint rubric (flowsint-tool-grades.yaml)       ──┤──▶ Generator ──▶ Toolshed (1318 .md rows)
Fire Enrich flow                                       │                   │
t00lsh3d config                                        │                   ▼
                                                  ────┘            Datapoint Atlas (75)
                                                                     Pivot Graph (100 rules)
                                                                           │
                                                                           ▼
                                                              sh3d dogf00dd (Python)
                                                                  │           │
                                                                  ▼           ▼
                                                    Run tool via pass-through   Parse output
                                                    (holehe, theHarvester…)    (tool-specific parsers)
                                                                  │
                                                                  ▼
                                                    Evaluate signal (confirmed/contradicted)
                                                                  │
                                                                  ▼
                                                    Update pivot confidence scores
```

## Components

| Layer | Tech | Purpose |
|---|---|---|
| **Toolshed** | Obsidian Base + YAML frontmatter | 1318 tool rows, each with enricher, datapoints, access_type, pricing_type, source |
| **Generator** | Python (generate_flowsint_osint_merge.py) | Merges OSINT Framework, FlowSint rubric, Fire Enrich, t00lsh3d config into unified rows |
| **Datapoint Atlas** | ~/.omp/datapoints.yaml | 75 datapoints with category (identity, contact, infrastructure…), sensitivity (high/medium/low) |
| **Pivot Graph** | ~/.omp/pivot_unlocks.yaml | 100 seed pivot rules: single-input transforms (34) and multi-input unlocks (66), each with confidence_default, risk, mechanism |
| **sh3d actions** | ~/.omp/sh3d.yaml | 53 actions including 30 pass-through CLIs (holehe, theHarvester, flaresolverr, starepo, gh-trending…) |
| **dogf00dd** | ~/.omp/flowsint-dogf00dd.py | Self-referential enrichment: reads pivot rules → maps atlas names to toolshed flat names → discovers tools → runs via pass-through → parses output → scores confidence |
| **meta** | ~/.omp/flowsint-meta.py | Coverage heatmap, pivot gap analysis, micro-module extraction, nuggets (cookbook-cook + leadgen hybrid) |

## Current State (2026-06-15)

### What's working
- **Generator** produces 1318 validated tool rows with 0 missing frontmatter, 0 broken links
- **Name bridge** maps ~30 atlas names to toolshed flat names (contact.email.address → email_address)
- **dogf00dd confirmed** 3 pivots:
  - email → provider (holehe, 20+ sites, confidence 0.70)
  - email → verified (holehe, 20+ sites, confidence 0.70)
  - email + MX → phone carrier (holehe, confidence 0.55)
- **Coverage heatmap** completes in 2.2s across 75 datapoints
- **Tool discovery** auto-detects 30 pass-through tools from sh3d.yaml
- **225 micro-modules** extracted (two-step enrichment chains)
- **Zod schema** validates all 1318 rows at `_validation/validate.ts`

### What's missing / needs improvement

| Gap | Impact | Likely Fix |
|---|---|---|
| **Name bridge incomplete** | 44/75 datapoints show "zero tools" (many are false zeros) | Expand ATLAS_TO_TOOLSHED mapping in flowsint-dogf00dd.py |
| **Only 3/100 pivots validated** | 97 pivot rules untested | Batch `sh3d run dogf00dd` on all 100, add missing pass-throughs |
| **Only 1 output parser (holehe)** | theHarvester, starepo, sherlock etc. have no parsers | Add _parse_theharvester, _parse_sherlock etc. |
| **No pipeline economics** | No cost/benefit for each enrichment path | Add confidence × pricing_type × speed scoring |
| **Gap discovery is manual** | 86 gap pivots with no runnable tool | Add leadgen waterfall: gh search → ddgr → manual add |
| **No transitive pivot detection** | 2-step chains are extracted but not validated | Run dogf00dd on step1 output → feed into step2 |
| **No regression tracking** | State changes aren't diffed | Add checkpoint/snapshot to meta SCOPE |

### Confidence distribution (100 pivot rules)
- High (0.6-0.8): 34
- Medium (0.3-0.6): 66
- Low (0.0-0.3): 0
- Very high (0.8-1.0): 0

After a full dogf00dd sweep, most medium should move to high/very-high.

### Coverage by datapoint category (75 total)
- Identity: 16 datapoints, most ~0-5 tools (many are bridge misses)
- Contact: 16, email has 94 tools, phone has ~0-2 (bridge miss)
- Infrastructure: 8, domain has 167 tools
- Verification: 9, most have 0 tools (real gap — these are atlas-only constructs)
- Document: 7, file has 91 tools, metadata has 0
- Geospatial: 6, all 0 tools (real gap)
- Organization: 1
- Breach: 1 (10 tools)
- Threat: 1 (70 tools)
- Public record: 1 (81 tools)

## Specific Ask for the Web Oracle

Given this pipeline, I'm looking for:

1. **What's the most leveraged next step** to get from 3/100 validated pivots to 80+/100?
2. **How should I structure the output parsers** for the ~30 pass-through tools so they generalize (regex patterns for success markers)?
3. **Where should the pipeline economics layer** live — in dogf00dd's per-pivot score, or as a separate cost/benefit table?
4. **What's the right abstraction for transitive pivot chains** — a DAG of tool outputs → tool inputs stored as a separate data structure, or computed on-the-fly?
5. **How would you prioritize the 86 gap pivots** — by datapoint sensitivity (high) × pivot confidence potential?
6. **Should the meta-analysis (coverage heatmap, gap detection, micro-modules) be pushed into the Obsidian Base as Dataview queries** instead of a standalone Python script?

## How to use this doc with the oracle
```bash
sh3d run oracle_ask --arg "QUERY=Review the attached toolspace pipeline doc in ~/.omp/toolspace-pipeline-brief.md (in ~/.omp/) and recommend the highest-leverage next 3 improvements."
```
