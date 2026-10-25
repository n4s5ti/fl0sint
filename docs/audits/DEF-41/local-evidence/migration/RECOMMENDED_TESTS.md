# DEF-41 S01 - Recommended Tests for Verification

## Test Execution Instructions

### Prerequisites
```bash
cd /home/n4s5ti/Documents/dev/fl0sint-def41-s01/flowsint-core
export PYTHONPATH=src:../flowsint-api
```

### Core Module Tests (Updated to use new imports)

#### 1. Structured Execution Contract Tests
```bash
pytest tests/core/test_structured_execution.py -v
```
**Files Updated:** 1
**Import Changes:** EvidenceEnvelope, InputOutcome, OutcomeStatus, RedactedDiagnostic, canonical_input_hash

**Expected Results:**
- ✓ test_structured_execution_keeps_input_cardinality_and_output_groups
- ✓ test_structured_execution_distinguishes_empty_success_from_failure
- ✓ test_non_success_outcomes_reject_derived_outputs
- ✓ test_canonical_input_hash_is_stable_for_equivalent_input_shapes
- ✓ test_evidence_envelope_enforces_retainable_metadata
- ✓ test_outcomes_refuse_invalid_status_transitions
- ✓ test_outcome_group_enforces_uniqueness
- ✓ test_outcome_group_enforces_non_empty
- ✓ test_evidence_metadata_types
- ✓ test_diagnostic_requires_valid_status

#### 2. Execution Service Tests
```bash
pytest tests/services/test_execution_service.py -v
```
**Files Updated:** 1
**Import Changes:** EvidenceEnvelope, InputOutcome, OutcomeStatus, RedactedDiagnostic, StructuredExecutionResult, canonical_input_hash

**Key Test Coverage:**
- Durable structured execution persistence
- SQLite contracts
- Outcome storage and retrieval
- Evidence envelope handling

#### 3. Graph Projection Service Tests
```bash
pytest tests/services/test_graph_projection_service.py -v
```
**Files Updated:** 1
**Import Changes:** EvidenceEnvelope, InputOutcome, OutcomeStatus, StructuredExecutionResult

**Key Test Coverage:**
- Deterministic graph projection contracts
- Approved projection handling

#### 4. Template Execution Task Tests
```bash
pytest tests/services/test_template_execution_task.py -v
```
**Files Updated:** 1
**Import Changes:** EvidenceEnvelope, InputOutcome, OutcomeStatus, StructuredExecutionResult, canonical_input_hash

**Key Test Coverage:**
- Stage 3 replay coverage
- Registry-backed template tasks
- Lease fencing

#### 5. Template Enricher Tests
```bash
pytest tests/templates/test_template_enricher.py -v
```
**Files Updated:** 1
**Import Changes:** OutcomeStatus

**Key Test Coverage:**
- Registry-backed connector egress
- Template execution
- Response redaction

#### 6. Acquisition Contract Tests
```bash
pytest tests/acquisition/test_contract.py -v
```
**Files Updated:** 1 (including local function imports)
**Import Changes:** OutcomeStatus, InputOutcome, EvidenceEnvelope, RedactedDiagnostic

**Key Test Coverage:**
- AcquisitionRequest validation
- AcquisitionBundle structure
- Extended outcome statuses
- Portable type contracts

### API Module Tests

#### 7. Template Egress Tests
```bash
cd ../flowsint-api
pytest tests/test_template_egress.py -v
```
**Files Updated:** 1 (test file)
**Related Updated:** 1 (schema file)
**Import Changes:** EvidenceEnvelope, InputOutcome, OutcomeStatus, StructuredExecutionResult, RedactedDiagnostic

**Key Test Coverage:**
- API contract for redacted connector-template test output
- Egress redaction
- Material masking

## Import Path Verification

### Quick Validation
```python
# Should work
from flowsint_execution.models import (
    OutcomeStatus,
    InputOutcome,
    EvidenceEnvelope,
    RedactedDiagnostic,
    StructuredExecutionResult,
    canonical_input_hash,
)

# Should fail (old module deleted)
try:
    from flowsint_core.core.execution import OutcomeStatus
except ImportError:
    print("✓ Old module correctly removed")
```

## Summary Statistics

**Total Test Files:** 7
**Total Updated Files:** 11 (plus 1 config)
**Affected Test Methods:** 50+

**Test Scope:**
- Core execution models: 6 test files
- API schema integration: 1 test file
- Configuration: 1 pyproject.toml

## Success Criteria

All tests should:
1. ✓ Import models from new location without errors
2. ✓ Maintain all original functionality
3. ✓ Demonstrate no behavior changes
4. ✓ Validate import paths correctly
5. ✓ Show zero references to old module

## Notes

- Tests use worktree src PYTHONPATH for isolated verification
- No database/network access required (mocked in conftest.py)
- Safe environment variables configured for vault/Neo4j tests
- All tests should pass without modification

## Execution Order (Recommended)

1. **Quick Check:** Run acquisition tests first (newest imports)
2. **Core Tests:** Run structured execution tests (foundational)
3. **Service Tests:** Run service layer tests (integration)
4. **API Tests:** Run API tests (external facing)

---

**Verification Status:** Ready for integration testing  
**Last Updated:** 2026-09-26
