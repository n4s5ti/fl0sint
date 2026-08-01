# Fl0sint Canonical Reference
## Architecture, Types, Enrichers, Pivots & Workflow
### Forensic-quality reference for auditing. Every claim cites source:line.
### Based on upstream FlowSINT ~2026-06-15, 72 enrichers (62 source + 10 template).

---

## 1. ARCHITECTURE OVERVIEW

```
flowsint-app  (React/Vite/TS)      Browser UI
    | Vite proxy /api -> :5001
flowsint-api  (FastAPI/Python)     REST + Celery tasks
    |-- postgres   users, investigations, flows, templates
    |-- neo4j      graph nodes/edges, sketch-scoped
    |-- redis      Celery broker
flowsint-core    (shared Python)   Enricher base, graph, vault, celery
flowsint-types   (Pydantic)        All entity types
flowsint-enrichers (Python)        62 source enrichers + registry
```

**Dep chain**: `flowsint-types -> flowsint-core -> {flowsint-enrichers, flowsint-api}`

---

## 2. TYPE SYSTEM

### Base Class
- `FlowsintType` (flowsint-types/src/flowsint_types/flowsint_base.py:6)
  - Extends `pydantic.BaseModel`, `ConfigDict(extra='allow')`
  - Field: `nodeLabel: Optional[str]` UI-readable graph label
  - `@flowsint_type` decorator registers into `TYPE_REGISTRY`

### Type Registry
- `TypeRegistry` (flowsint-types/src/flowsint_types/registry.py:20)
- `TYPE_REGISTRY.register(cls)` stores by `cls.__name__` + lowercase
- `load_all_types()` auto-discovers via pkgutil
- Lookup: `get_type('Domain')`, `get_type('domain')`

### Registered Types
39 FlowsintType subclasses (MapTypes forensic catalog):
**Network**: Domain, Ip, ASN, CIDR, Port, DNSRecord, Whois, SSLCertificate
**Identity**: Individual, Email, Phone, Username, SocialAccount, Gravatar, Alias, Affiliation
**Organization**: Organization, Website, WebTracker
**Risk/Asset**: Wallet (CryptoWallet), CryptoNFT, CryptoWalletTransaction, Leak, Breach, Device, Malware, ReputationScore, RiskProfile, Weapon, BankAccount, CreditCard, Credential
**Content**: Phrase, Message, Document, File, Script
**Location**: Address
**Operational**: Scan, Analysis, Session

Primary field: `json_schema_extra={"primary": True}` marks the field for string->object coercion in Enricher.preprocess().
Nested typed fields provide implicit pivot hints: Whois.domain:Domain, Website.domain:Domain, SocialAccount.username:Username.

### Type Pivots (implicit)
Types connect through enrichers. InputType=X, OutputType=Y defines pivot X->Y:
- Email -> Domain (email_to_domain)
- Domain -> Ip (domain_to_ip)
- Ip -> Domain (ip_to_domain)
- Email -> Individual (email_to_intelligence)
- Individual -> Organization (individual_to_organization)
- Domain -> Website (domain_to_website)

No explicit pivot model exists - enricher registry serves as pivot table.

---

## 3. ENRICHER SYSTEM

### Base Class (flowsint-core/src/flowsint_core/core/enricher_base.py:50)

```
Enricher(ABC):
    InputType   class attr (Pydantic model, NOT List)
    OutputType  class attr

    @classmethod name(), category(), key(), description()
    @classmethod params_schema(), icon(), required_params()

    async execute(values):       Main entry point
        await async_init()       Resolve vault secrets
        preprocessed = preprocess(values)   Validate -> InputType[]
        results = await scan(preprocessed)  [abstract] Do the work
        processed = postprocess(results)    Optional cleanup
        graph_service.flush()    Commit Neo4j batch
        return processed

    # Graph helpers (called in scan/postprocess):
    create_node(obj)             -> Neo4j node with sketch_id
    create_relationship(a,b,lbl) -> Neo4j edge
```

### Enricher Lifecycle
1. `async_init()` - resolve vault secrets, set up HTTP clients
2. `preprocess(values)` - validate raw input -> InputType instances (skips invalid)
3. `scan(preprocessed)` - [abstract] fetch/process data
4. `postprocess(results, input)` - optional cleanup/relationship creation
5. `graph_service.flush()` - commit batched Neo4j writes

### Registry (flowsint-enrichers/src/flowsint_enrichers/registry.py)
- `ENRICHER_REGISTRY` global singleton
- `@flowsint_enricher` decorator registers classes
- `load_all_enrichers()` scans package via os.walk, imports all .py files

### Source Enrichers (62)

| Category | Count | Enrichers |
|---|---|---|
| Asn | 1 | asn_to_cidrs |
| Cidr | 1 | cidr_to_ips |
| CryptoWallet | 2 | to_nfts, to_transactions |
| Domain | 11 | to_ip, to_asn, to_subdomains, to_whois, to_whois_history, to_history, to_dehashed, to_website, to_root_domain, to_tls, to_dummy |
| Email | 7 | to_domain, to_domains, to_username, to_gravatar, to_intelligence, to_device_hudsonrock, to_breaches |
| Individual | 2 | to_domains, to_organization |
| Ip | 6+1 | to_asn, to_domain, to_infos, to_intelligence, to_ports, to_fraudscore (+ip_to_dummy_domains) |
| Organization | 9 | to_domains, to_asn, to_infos + 6 fire_enrich_* |
| Phone | 3 | to_carrier, to_infos, to_device_hudsonrock |
| Phrase | 3 | to_contact_individuals, to_contact_emails, to_contact_phones |
| Social | 6 | to_sherlock, to_maigret, to_blackbird, to_dehashed, to_hudsonrock, to_osintgram |
| Website | 7 | to_crawler, to_domain, to_links, to_subdomains, to_text, to_webtrackers, to_fire_enrich_company |
| External | 1 | n8n_connector |

### Template Enrichers (10, DB-stored)

| Name | Category | Input->Output |
|---|---|---|
| person-lookup | Email | Email->Individual |
| thatsthem-lookup | Email | Email->Individual |
| spokeo-lookup | Email | Email->Individual |
| fastpeoplesearch-lookup | Email | Email->Individual |
| truecaller-lookup | Phone | Phone->Individual |
| individual-to-socials-blackbird | Individual | Individual->SocialAccount |
| linkedin-people-search-opencli | Individual | Individual->Individual |
| twitter-profile-opencli | Username | Username->SocialAccount |
| organization-about-page | Organization | Organization->Any |
| organization-contact-page | Organization | Organization->Any |

### Template Schema (flowsint-core/src/flowsint_core/templates/types.py:138)
```
Template:
    name, category, version
    input:  { type, key? }
    request: { method: GET|POST, url, headers[], body?, timeout? }
    response: { expect: json|xml|text, map?: {field->jsonPath}, transform? }
    output: { type }
    secrets: [{ name, required? }]   # vault integration
    retry: { max_retries, backoff_factor? }
```
SSRF protection, vault secrets, XML parsing, JSON mapping included.
`TemplateEnricher` (flowsint-core/src/flowsint_core/core/template_enricher.py) extends Enricher.

---

## 4. FLOW SYSTEM

### Flow Model
- `flows` table: `{id, name, description, category[], flow_schema: JSONB}`
- `Flow` model (flowsint-core/src/flowsint_core/core/models.py:198): id(UUID), name(Text), description(Text nullable), category(JSON nullable), flow_schema(JSON nullable), created_at, last_updated_at
- `FlowRepository` (flowsint-core/src/flowsint_core/core/repositories/flow_repository.py:12): returns all ordered by `last_updated_at DESC`; filters in Python by case-insensitive category membership
- `FlowService` (flowsint-core/src/flowsint_core/core/services/flow_service.py:27): wired with FlowRepository, CustomTypeRepository, SketchRepository, InvestigationRepository
- `FlowService.create()`: generates UUID, sets timestamps, commits
- `FlowService.update()`: applies every field directly; special: category containing `SocialAccount` auto-appends `Username`
- `flow_schema = { nodes: FlowNode[], edges: FlowEdge[] }`
- `FlowNode`: `{ id, type: 'enricher'|'type', position, data: EnricherNodeData }`
- `FlowEdge`: `{ id, source, target, type }` connects enricher nodes in DAG

### Flow Editor (frontend: flowsint-app/src/components/flows/editor.tsx)
- React Flow-based visual canvas
- Node types: `enricher` (EnricherNode), `type` (TypeNode)
- Raw materials: `GET /api/flows/raw_materials` returns all enrichers for palette
- Pivot validation: edges auto-verify output->input type compatibility
- Flow store: zustand (`flowsint-app/src/stores/flow-store.ts`) manages nodes/edges/UI state

### Flow Execution
1. `POST /api/flows/{id}/launch` with `{node_ids, sketch_id}`
2. Load flow schema, retrieve Neo4j nodes, compute flowBranches (DAG paths)
3. Dispatch `run_flow` Celery task
4. Orchestrator executes enrichers sequentially, passing outputs as next inputs

### Flow Computation (`POST /api/flows/compute`)
- Input: `{nodes, edges, inputType}`
- Output: `{flowBranches, initialData}`
- Simulation: `POST /api/flows/simulate-step` tests individual steps

---

## 5. GRAPH MODEL

### Neo4j Schema
- Node labels: `:individual`, `:email`, `:domain`, `:ip`, `:socialaccount`, etc.
- Key properties: `sketch_id`, `nodeLabel`, `created_at`, `id` (element ID), all typed fields
- Edges: `[:RELATED_TO]`, `[:FOUND_IN_BREACH]`, `[:HAS_DOMAIN]`, custom labels

### GraphNode (flowsint-core/src/flowsint_core/core/graph/types.py:23)
```
GraphNode:
    id: str|None, nodeLabel: str, nodeType: str
    nodeProperties: Any, nodeMetadata: {created_at}
    nodeSize, nodeColor, nodeIcon, nodeFlag, nodeShape
    x, y: float (canvas position)
```

### Graph Service (flowsint-core/src/flowsint_core/core/graph/service.py:28)
- `create_node_from_flowsint_type(obj)` - Pydantic -> Neo4j
- `create_relationship(from, to, label)` - connects nodes
- `get_sketch_graph()` -> `GraphData{nodes, edges}`
- `get_nodes_by_ids_for_task(ids)` -> enricher dispatch
- Batching: accumulate, flush()

---

## 6. END-TO-END WORKFLOW

### Investigation Lifecycle
1. Create Investigation: `POST /api/investigations/create`
2. Create Sketch: `POST /api/sketches/create`
3. Import Data: `POST /api/sketches/{id}/import/execute` (bulk JSON/CSV)
4. Add Node: `POST /api/sketches/{id}/nodes/add` (single)
5. Launch Enricher: `POST /api/enrichers/{name}/launch` (on selected nodes)
6. Launch Flow: `POST /api/flows/{id}/launch` (multi-step pipeline)
7. View Graph: `GET /api/sketches/{id}/graph`
8. Write Analysis: text doc in investigation

### UI Navigation
```
/dashboard                                investigation list
/dashboard/enrichers                      templates (10)
/dashboard/flows                          flow list
/dashboard/flows/$id                      flow editor (React Flow)
/dashboard/investigations/$id             investigation detail
/dashboard/investigations/$id/graph/$sid  graph viewer
```

### Enricher Dispatch
1. UI calls `POST /api/enrichers/{name}/launch` {node_ids, sketch_id}
2. API retrieves Neo4j nodes via GraphService
3. Dispatches `run_enricher` or `run_template_enricher` Celery task
4. Worker: instantiate enricher -> execute(values) -> flush graph

### Import Pipeline
1. Upload file -> `POST /api/sketches/{id}/import/analyze`
2. ImportService.analyze_file() -> FileParseResult
3. User maps columns in UI
4. `POST /api/sketches/{id}/import/execute` with entity_mappings_json
5. ImportService.execute_import() -> Neo4j nodes

### Results visibility (SSE-driven refresh cycle)
1. Graph page installs useGraphRefresh(sketch_id) on mount (flowsint-app/src/components/sketches/index.tsx:60)
2. Hook connects to SSE stream: GET /api/events/sketch/{id}/status/stream (flowsint-app/src/hooks/use-graph-refresh.ts:27)
3. Celery task emits COMPLETED status event on enricher/flow finish (flowsint-core/src/flowsint_core/tasks/enricher.py:55)
4. SSE listener triggers refetchGraph() which re-queries GET /api/sketches/{id}/graph -> re-renders ForceGraph canvas
5. Manual refresh via toolbar handleRefresh as fallback (flowsint-app/src/components/sketches/toolbar.tsx:134)

---

## 7. POSTGRES DATA MODEL

| Table | Purpose |
|---|---|
| profiles | User accounts (email, hashed_password, name) |
| investigations | Cases (name, description, status, owner_id) |
| sketches | Graph canvases (title, investigation_id) |
| flows | Saved enricher pipelines (flow_schema JSONB) |
| enricher_templates | Declarative enrichers (content JSONB) |
| custom_types | User type extensions |
| analyses | Text docs |
| keys | API keys/credentials |
| chats | AI chat |
| logs | Event log |

### Auth
- JWT HS256 via `jose`, secret in `AUTH_SECRET`
- `POST /api/auth/register`, `POST /api/auth/token`
- `DEV_AUTO_LOGIN=email` bypass in dev

---

## 8. KNOWN GAPS

1. $RefreshReg$: vite@8 + @vitejs/plugin-react@4 mismatch. Pin vite@5.3.1 + plugin-react@4.3.1
2. NODE_ENV=production global breaks import.meta.env.DEV
3. CORS: API at :5001 blocked from :5173. Set ALLOWED_ORIGINS
4. celeryd: enricher launch schedules tasks; needs worker running
5. Neo4j password: container must match API NEO4J_PASSWORD
6. Template enricher duality: registry checked first, then templates
7. Type casing: Ip/IP/ips inconsistent
8. Test gaps: duplicate functions, missing test_ prefix
9. No explicit pivot registry - implicit through enricher chains
10. API-key enrichers fail silently without keys
11. flow_schema unvalidated: Optional[Dict] at API boundary, no FlowNode/FlowEdge validation until compute/launch (flowsint-api/app/api/schemas/flow.py:8)
12. Runtime edge semantics mismatch: compute_flow_branches records step.inputs but FlowOrchestrator ignores them, passes raw previous outputs (flowsint-core/src/flowsint_core/core/orchestrator.py:368)
13. Custom type flow dead code: FlowService detects custom type, loads all flows, then immediately returns [] (flowsint-core/src/flowsint_core/core/services/flow_service.py:48)
14. Compute request mismatch: API expects inputType but frontend sends initialValue (flowsint-api/app/api/routes/flows.py:48 vs flowsint-app/src/components/flows/editor.tsx:399)
15. Unauthenticated raw_materials: GET /api/flows/raw_materials and /input_type/{type} skip auth dependency (flowsint-api/app/api/routes/flows.py:82)
16. Preview/stub disparity: flow preview uses hardcoded sample outputs; real launch runs registered enrichers via Celery (flowsint-api/app/api/routes/flows.py:476 vs flowsint-core/src/flowsint_core/tasks/flow.py:55)
17. Enricher lookup coupling: runtime derives name from step.nodeId.split('-')[0], coupling execution to frontend node id convention (flowsint-core/src/flowsint_core/core/orchestrator.py:228)
18. Template postprocess always creates HAS_SOCIAL_ACCOUNT relationship regardless of template type (flowsint-core/src/flowsint_core/core/template_enricher.py:537)
19. JSON import returns total_entities=0 vs TXT which returns line count (flowsint-core/src/flowsint_core/imports/json/parse_json.py:73)
20. StepSimulationRequest defined but unused endpoint (flowsint-api/app/api/routes/flows.py:59)
21. InvestigationService.get_sketches raises NotFoundError for empty investigation — frontend expects empty array (flowsint-core/src/flowsint_core/core/services/investigation_service.py:62 vs flowsint-app/src/components/dashboard/investigation/sketches-section.tsx:16)
22. Graph file path stale: graph.py → flowsint-core/src/flowsint_core/core/graph/ package (flowsint-core/src/flowsint_core/core/graph/__init__.py:1)
23. Duplicate enricher name: website_to_fire_enrich_company registered in both organization/fire_enrich_company.py and website/to_fire_enrich_company.py — import order determines which wins (flowsint-enrichers/src/flowsint_enrichers/registry.py:30)
24. Enricher count: 64 enricher classes cataloged (not 62) — MapEnrichers found two additional classes I missed

---

## 9. ENV VARS

DATABASE_URL, NEO4J_URI_BOLT, NEO4J_USERNAME, NEO4J_PASSWORD, REDIS_URL,
AUTH_SECRET, MASTER_VAULT_KEY_V1, ALLOWED_ORIGINS, DEV_AUTO_LOGIN,
VITE_API_URL, VITE_DEV_AUTO_LOGIN, FIRECRAWL_API_KEY, DEHASHED_API_KEY,
HIBP_API_KEY, WHOXY_API_KEY
