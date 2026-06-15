#!/usr/bin/env python3
"""
enrich_sheet.py — Standalone Sheet Enrichment Script

Reads emails from Peptide Buyers sheet, runs a flowsint enricher
directly (no API/auth needed), writes results back to the sheet.

Usage:
  # Fill phone + linkedin via DeHashed (needs DEHASHED_API_KEY):
  export DEHASHED_API_KEY=your_key
  uv run python3 scripts/enrich_sheet.py email_to_intelligence

  # Test with no-auth enricher (writes domain to linkedin col):
  uv run python3 scripts/enrich_sheet.py email_to_domain

  # Fill social accounts:
  uv run python3 scripts/enrich_sheet.py email_to_username

Prerequisites:
  - gog CLI authenticated (michael@agentsec.ai)
  - `uv run` in project root (or pip install -e all subpackages)
"""

import asyncio
import json
import os
import subprocess
import sys
from typing import Any, Optional

SPREADSHEET_ID = "1RS4Z0PXO_dbbkNhPoHLJ5PiWpYSUEfAL6Fvc2XLOOUc"
SHEET_RANGE = "main!A:G"

# Column indices: 0-based
COL_EMAIL = 0     # A
COL_PHONE = 3     # D
COL_LINKEDIN = 6  # G

# Enricher name -> (import path, class_name, output_field_extractors)
# extractors: dict of col_name -> (output_key, parse_fn)
ENRICHER_MAP = {
    "email_to_intelligence": {
        "module": "flowsint_enrichers.email.to_dehashed",
        "class": "EmailToDehashed",
        "extract": {
            "phone": ("phone_numbers", lambda v: (
                (v[0].get("number") or v[0].get("phone") or str(v[0])) if v and isinstance(v, list) and isinstance(v[0], dict)
                else str(v[0]) if v and isinstance(v, list)
                else None
            )),
            "linkedin": ("social_media_profiles", lambda v: next(
                (s for s in (v or []) if "linkedin.com" in str(s).lower()), None
            )),
        },
    },
    "email_to_domain": {
        "module": "flowsint_enrichers.email.to_domain",
        "class": "EmailToDomainEnricher",
        "extract": {
            "linkedin": ("domain", lambda v: v),  # writes domain as placeholder
        },
    },
    "email_to_username": {
        "module": "flowsint_enrichers.email.to_username",
        "class": "EmailToUsernameEnricher",
        "extract": {
            "linkedin": ("value", lambda v: v),   # writes username as placeholder
        },
    },
}


def read_sheet() -> list:
    cmd = ["gog", "sheets", "get", SPREADSHEET_ID, SHEET_RANGE, "-j", "--results-only"]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print("ERROR: gog get failed: " + result.stderr, file=sys.stderr)
        sys.exit(1)
    return json.loads(result.stdout)


def write_cell(row_num: int, col_letter: str, value: str) -> bool:
    cell = "main!" + col_letter + str(row_num)
    cmd = ["gog", "sheets", "update", SPREADSHEET_ID, cell, value]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print("  WARN: write " + cell + " failed: " + result.stderr.strip())
        return False
    return True


async def run_enricher(enricher_name: str, emails: list) -> Optional[list]:
    """Run an enricher by importing its class directly."""
    info = ENRICHER_MAP.get(enricher_name)
    if not info:
        print("ERROR: Unknown enricher '" + enricher_name + "'")
        print("  Available: " + ", ".join(ENRICHER_MAP.keys()))
        return None

    # Import the enricher class
    import importlib
    mod = importlib.import_module(info["module"])
    EnricherClass = getattr(mod, info["class"])

    # Build input objects from emails
    InputType = EnricherClass.InputType
    inputs = []
    for e in emails:
        try:
            inputs.append(InputType(email=e))
        except Exception as ex:
            print("  WARN: skip " + e + ": " + str(ex))

    if not inputs:
        print("  ERROR: No valid input objects created")
        return None

    # Create enricher instance with params from env
    params = {}
    for p in EnricherClass.get_params_schema():
        env_val = os.environ.get(p["name"])
        if env_val:
            params[p["name"]] = env_val

    enricher = EnricherClass(params=params)

    # For enrichers without vault/params, skip async_init
    has_params = bool(EnricherClass.get_params_schema())
    if has_params and not params:
        print("  NOTE: " + enricher_name + " needs params (missing from env)")
        # Still try to run - scan may handle missing key gracefully

    # Preprocess + scan
    try:
        cleaned = enricher.preprocess(inputs)
        results = await enricher.scan(cleaned)
    except Exception as ex:
        print("  ERROR: enricher failed: " + str(ex))
        return None

    print("  Got " + str(len(results)) + " results")
    return results


def extract_field(result_item: Any, extractor_def: dict) -> str:
    """Extract a field from an enricher result using the defined extractor."""
    output_key, parse_fn = extractor_def
    if isinstance(result_item, dict):
        value = result_item.get(output_key)
    else:
        value = getattr(result_item, output_key, None)
    try:
        return parse_fn(value)
    except Exception:
        return None


async def main():
    if len(sys.argv) < 2:
        print("Usage: uv run python3 scripts/enrich_sheet.py <enricher_name>")
        print("  Available: " + ", ".join(ENRICHER_MAP.keys()))
        sys.exit(1)

    enricher_name = sys.argv[1]
    info = ENRICHER_MAP.get(enricher_name)
    if not info:
        print("Unknown enricher. Available: " + ", ".join(ENRICHER_MAP.keys()))
        sys.exit(1)

    print("=== enrich_sheet.py ===")
    print("Enricher: " + enricher_name)
    extractors = info["extract"]
    print("Writing to columns: " + ", ".join(extractors.keys()))

    # Read sheet
    print("\n[1] Reading sheet...")
    rows = read_sheet()
    if not rows:
        print("ERROR: No data")
        sys.exit(1)
    data_rows = rows[1:]  # skip header
    print("  " + str(len(data_rows)) + " data rows")

    # Find targets
    targets = []
    for i, row in enumerate(data_rows):
        email = row[COL_EMAIL].strip() if len(row) > COL_EMAIL and row[COL_EMAIL] else ""
        if not email:
            continue
        needs = []
        col_map = {"phone": (COL_PHONE, "D"), "linkedin": (COL_LINKEDIN, "G")}
        for field_name in extractors:
            idx, letter = col_map[field_name]
            val = row[idx].strip() if len(row) > idx and row[idx] else ""
            if not val:
                needs.append((field_name, letter))
        if needs:
            targets.append((i, email, needs))

    print("  " + str(len(targets)) + " rows need enrichment")

    # Run enricher
    emails = [t[1] for t in targets]
    if not emails:
        print("DONE: All filled")
        return

    print("\n[2] Running " + enricher_name + "...")
    results = await run_enricher(enricher_name, emails)
    if results is None:
        print("FAILED")
        sys.exit(1)

    # Write results
    print("\n[3] Writing to sheet...")
    written = 0
    for ri, (row_idx, email, needs) in enumerate(targets):
        sheet_row = row_idx + 2  # header + 0-based index
        result_item = results[ri] if ri < len(results) else None
        if result_item is None:
            continue

        for field_name, col_letter in needs:
            extractor = extractors[field_name]
            value = extract_field(result_item, extractor)
            if value:
                if write_cell(sheet_row, col_letter, str(value)):
                    print("  " + col_letter + str(sheet_row) + " = " + str(value)[:80])
                    written += 1

    print("\n[DONE] Wrote " + str(written) + " cells")


if __name__ == "__main__":
    asyncio.run(main())
