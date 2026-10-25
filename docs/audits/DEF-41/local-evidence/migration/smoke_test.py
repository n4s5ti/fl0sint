#!/usr/bin/env python3
"""Smoke test for canonical public import with cleared environment and no infra."""

import subprocess
import sys
import os
from pathlib import Path

# Worktree paths
WORKTREE = Path("/home/n4s5ti/Documents/dev/fl0sint-def41-s01")
CORE_SRC = WORKTREE / "flowsint-core" / "src"
API_SRC = WORKTREE / "flowsint-api"

def run_import_test(code: str, description: str, pythonpath: str = None) -> dict:
    """Run an import test in an isolated subprocess."""
    env = {
        "PATH": os.environ.get("PATH", ""),
        "HOME": os.environ.get("HOME", ""),
    }
    if pythonpath:
        env["PYTHONPATH"] = pythonpath
    
    result = subprocess.run(
        [sys.executable, "-c", code],
        env=env,
        capture_output=True,
        text=True,
        cwd=str(WORKTREE),
    )
    
    return {
        "description": description,
        "code": code,
        "return_code": result.returncode,
        "stdout": result.stdout,
        "stderr": result.stderr,
        "success": result.returncode == 0,
    }

def main():
    """Run all smoke tests."""
    tests = [
        # Test 1: Cleared environment smoke test (canonical public import)
        {
            "code": f"""
import sys
sys.path.insert(0, '{CORE_SRC}')
from flowsint_execution.models import OutcomeStatus, InputOutcome, EvidenceEnvelope, RedactedDiagnostic, StructuredExecutionResult, canonical_input_hash
print('SUCCESS: All canonical models imported')
print(f'OutcomeStatus values: {{list(OutcomeStatus)}}')
""",
            "description": "Canonical public import with worktree src PYTHONPATH",
            "pythonpath": str(CORE_SRC),
        },
        # Test 2: Verify old module is gone
        {
            "code": f"""
import sys
sys.path.insert(0, '{CORE_SRC}')
try:
    from flowsint_core.core.execution import OutcomeStatus
    print('FAIL: Old module still exists')
    sys.exit(1)
except ImportError as e:
    print('SUCCESS: Old module correctly removed')
    print(f'Error: {{e}}')
""",
            "description": "Verify old core.execution module is deleted",
            "pythonpath": str(CORE_SRC),
        },
        # Test 3: API schema import
        {
            "code": f"""
import sys
sys.path.insert(0, '{CORE_SRC}')
sys.path.insert(0, '{API_SRC}')
from flowsint_execution.models import RedactedDiagnostic
from flowsint_api.app.api.schemas.enricher_template import ConnectorTestOutcome
print('SUCCESS: API schema import successful')
""",
            "description": "API schema import using relocated models",
            "pythonpath": f"{CORE_SRC}:{API_SRC}",
        },
    ]
    
    results = []
    for test in tests:
        result = run_import_test(
            test["code"],
            test["description"],
            test.get("pythonpath"),
        )
        results.append(result)
        
        print(f"\n{'='*70}")
        print(f"Test: {result['description']}")
        print(f"Status: {'✓ PASS' if result['success'] else '✗ FAIL'}")
        if result['stdout']:
            print(f"Output: {result['stdout']}")
        if result['stderr']:
            print(f"Stderr: {result['stderr']}")
        if result['return_code'] != 0:
            print(f"Return code: {result['return_code']}")
    
    # Summary
    print(f"\n{'='*70}")
    print("SUMMARY")
    print(f"{'='*70}")
    passed = sum(1 for r in results if r['success'])
    total = len(results)
    print(f"Tests passed: {passed}/{total}")
    
    for result in results:
        status = "✓" if result['success'] else "✗"
        print(f"{status} {result['description']}")
    
    # Return overall success
    return all(r['success'] for r in results)

if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
