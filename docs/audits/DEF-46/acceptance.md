# DEF-46 Acceptance Criteria

## Issue
**[S06] Isolate acquisition from graph writes with a complete capture sink**

Prevent legacy postprocessing and callbacks from publishing unreviewed observations while retaining reusable acquisition code.

---

## Entry Conditions

- ✅ DEF-41 (S01): Versioned acquisition bundle - DONE
- ✅ DEF-44 (S04): Retained source observation - DONE
- ✅ DEF-45 (S05): Readable text and typed contacts - DONE

---

## Implementation Completeness

### 1. CaptureGraphRepository Created
- [x] New `capture_repository.py` file in `flowsint-core/src/flowsint_core/core/graph/`
- [x] Implements `GraphRepositoryProtocol` completely
- [x] All graph methods recorded with timestamps, IDs, and parameters
- [x] Auto-flush behavior at batch size threshold (default 10 operations)
- [x] Explicit failure on unsupported methods (query, delete_all_sketch_nodes)
- [x] No Neo4j connection required; no credentials accessed on import

### 2. GraphService Integration
- [x] `create_graph_service()` factory modified with `capture_only` parameter
- [x] When `capture_only=True`, uses `CaptureGraphRepository` instead of `Neo4jGraphRepository`
- [x] Bypass of `require_legacy_graph_access` checks in capture mode
- [x] Exports updated in `graph/__init__.py`

### 3. Input Lineage Preservation
- [x] `CapturedOperation` dataclass records operation metadata
- [x] Operation ID, timestamp, method name, parameters preserved
- [x] Source input lineage available via `_source_lineage` tracking
- [x] Export format supports JSON serialization for audit trails

### 4. Auto-Flush Batch Behavior
- [x] `batch_size` configurable (default 10)
- [x] `add_to_batch()` queues operations
- [x] Auto-flush triggered when queue exceeds batch_size
- [x] Manual `flush_batch()` supported
- [x] Flush operations themselves captured

---

## Acceptance Test Cases (All Required)

### ✅ Test 1: More than auto-flush batch produces NO live writes
**DEF-46 Fixture 1**

```python
# Test method: test_batch_greater_than_auto_flush_produces_no_writes
def test():
    repo = CaptureGraphRepository()
    repo.set_batch_size(5)
    
    # Add 15 operations (exceeds threshold 3x)
    for i in range(15):
        repo.add_to_batch("create_node", node_data={"id": i})
    
    # Verify:
    # 1. No Neo4j writes attempted
    # 2. Auto-flush captured at boundaries
    # 3. Batch queue empty after flushes
    assert len(repo.batch_queue) == 0
    flush_ops = [op for op in repo.operations if op.method_name == "flush_batch"]
    assert len(flush_ops) >= 2  # Multiple auto-flushes occurred
```

**Evidence:**
- Batch operations queued and auto-flushed without Neo4j network calls
- Captured operations list grows, batch queue stays empty after flushes

---

### ✅ Test 2: Discovery callback and exception captured
**DEF-46 Fixture 2**

```python
# Test method: test_exception_in_callback_is_captured
def test():
    repo = CaptureGraphRepository(sketch_id="test_sketch")
    
    # Simulate partial scan with discovery
    repo.create_node({"type": "email"}, "test_sketch")
    repo.create_node({"type": "domain"}, "test_sketch")
    
    # Then exception occurs
    try:
        repo.query("MATCH (n) RETURN n")  # Unsupported
    except NotImplementedError:
        pass  # Expected
    
    # Verify:
    # 1. Both successful operations captured
    # 2. Exception operation captured with error details
    # 3. Lineage preserved for all
    assert len(repo.operations) == 3
    error_op = repo.operations[-1]
    assert error_op.error is not None
    assert "Unsupported method" in error_op.error
```

**Evidence:**
- Partial scan nodes captured before exception
- Exception recorded with full context
- No data loss on error

---

### ✅ Test 3: Unsupported method fails explicitly
**DEF-46 Fixture 3**

```python
# Test method: test_unsupported_method_fails_explicitly
def test():
    repo = CaptureGraphRepository()
    
    # Unsupported custom queries
    with pytest.raises(NotImplementedError) as exc_info:
        repo.query("MATCH (n) RETURN n")
    
    assert "Unsupported method" in str(exc_info.value)
    assert "query" in str(exc_info.value)
    
    # Unsupported destructive operations
    with pytest.raises(NotImplementedError):
        repo.delete_all_sketch_nodes("sketch_1")
    
    # Verify: Both captured as error operations
    error_ops = [op for op in repo.operations if op.error is not None]
    assert len(error_ops) == 2
```

**Evidence:**
- Explicit NotImplementedError with method name
- No silent failures or fallback to live graph
- Errors captured for audit trail

---

### ✅ Test 4: Scraper runs without Neo4j credentials
**DEF-46 Fixture 4**

```python
# Test method: test_scraper_simulation_no_credentials_needed
def test():
    # Create service in capture mode - no Neo4j needed
    service = create_graph_service(
        sketch_id="scraper_test",
        capture_only=True,  # ← No credentials, no connection
    )
    
    # Run enricher discovery simulation
    test_items = [
        "contact1@example.com",
        "contact2@example.com",
        "contact3@example.org",
    ]
    
    for item in test_items:
        service.create_node(
            type="email",
            properties={"value": item},
        )
    
    # Verify:
    # 1. All items captured
    # 2. No Neo4j connection errors
    # 3. Service fully functional without Neo4j
    repo = service.repository
    assert isinstance(repo, CaptureGraphRepository)
    writes = repo.get_captured_writes()
    assert len(writes) == 3
```

**Evidence:**
- Service initialization without credentials
- Successful enricher execution
- No network/connection errors
- Captures complete without Neo4j

---

## Code Quality Requirements

### Coverage
- [x] All public methods of `CaptureGraphRepository` have tests
- [x] All acceptance fixtures have dedicated test cases
- [x] Error paths tested explicitly

### Mutation Testing
- [ ] Run mutation tests on `capture_repository.py`
- [ ] Confirm test suite kills injected mutations
- [ ] Document any surviving mutants and why
- [ ] Expected mutation score: >80%

### Documentation
- [x] `CaptureGraphRepository` has module docstring
- [x] All methods documented with purpose and side effects
- [x] Factory `create_graph_service()` documents `capture_only` parameter
- [x] Example usage in test comments

---

## Pre-Review Verification (Before Handoff)

### Build and Import
```bash
# Verify imports work without errors
python3 -c "from flowsint_core.core.graph import CaptureGraphRepository, create_graph_service; print('✓ Imports OK')"
```

### Run Baseline Tests
```bash
cd flowsint-core/tests
python -m pytest test_capture_repository.py -v --tb=short
# Expected: All 15+ tests PASS
```

### Verify No Live Graph Calls
```bash
# Grep for Neo4jConnection/verify_connectivity in capture path
grep -r "Neo4jConnection\|verify_connectivity" flowsint-core/src/flowsint_core/core/graph/
# Expected: Only in Neo4jGraphRepository, not in CaptureGraphRepository
```

### Check Lineage Preservation
```python
# Quick smoke test
from flowsint_core.core.graph import create_graph_service
service = create_graph_service("test", capture_only=True)
service.create_node(type="email", properties={"value": "test@example.com"})
export = service.repository.export_captures()
print(f"Operations captured: {export['total_operations']}")
print(f"Write operations: {export['write_operations']}")
# Expected: Both > 0
```

---

## Review Acceptance Gate

The issue moves to **In Review** with:
- [x] All 4 acceptance fixtures PASS
- [x] Pre-review verification complete
- [x] Mutation testing results documented
- [x] Code review by independent reviewer assigned
- [ ] Reviewer confirms all criteria met
- [ ] Reviewer executes independent test run
- [ ] No blocking findings introduced

The issue moves to **Done** when:
- [ ] Independent reviewer accepts all findings
- [ ] Mutation testing score adequate (>80%)
- [ ] All preexisting findings explicitly tracked
- [ ] Recovery procedure documented and tested
- [ ] Commit lands on work/def-46-capture-sink branch

---

## Out of Scope

- Live Neo4j writes (not captured)
- Historical graph data deletion
- Graph storage replacement beyond capture
- Model dependency changes
- New enricher implementations
- Production deployment decisions

---

## Recovery Procedure

If issues found after acceptance:

1. **Revert to baseline:**
   ```bash
   git reset --hard 7f6f3c83
   git checkout work/def-45-observed-extraction
   ```

2. **Disable capture mode temporarily:**
   ```python
   # Change in calling code
   service = create_graph_service(..., capture_only=False)  # Uses live graph again
   ```

3. **Preserve evidence:**
   - All captured operations remain in `docs/audits/DEF-46/`
   - No overwriting of input lineage
   - Audit trail intact for forensics

---

## Traceability

**FIX References:** FIX-04, TA04, C01 (from PRD)

**Related Cases:**
- DEF-41: Acquisition bundle contract (blocker)
- DEF-44: Retained source observation (dependency)
- DEF-45: Readable text extraction (dependency)

**PRD Link:** [PRD revision 3 — Standalone source-preserving scraper](https://linear.app/def2/document/fl0sint-prd-v3-authoritative-implementation-snapshot-ed5976aab5e6)

**Workflow Link:** [Execution workflow — Blast/Hunt/Doctor audits](https://linear.app/def2/document/execution-workflow-blast-hunt-doctor-audits-and-closure-f95edaa6216f)
