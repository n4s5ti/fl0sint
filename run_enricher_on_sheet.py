#!/usr/bin/env python3
"""
Enricher Integration Script (Option A: Direct API)
Reads email from Peptide Buyers sheet, launches email_to_intelligence
enricher via Flowsint API, writes phone + linkedin results back.

Usage:
  export DEHASHED_API_KEY=your_key_here
  python3 run_enricher_on_sheet.py

Prerequisites:
  - gog CLI authenticated (michael@agentsec.ai)
  - Flowsint API running at VITE_API_URL (default http://127.0.0.1:5001)
  - DEHASHED_API_KEY in env or flowsint vault
"""

import json
import os
import subprocess
import sys
import time
import urllib.request
import urllib.error

SPREADSHEET_ID = "1RS4Z0PXO_dbbkNhPoHLJ5PiWpYSUEfAL6Fvc2XLOOUc"
API_URL = os.environ.get("VITE_API_URL", "http://127.0.0.1:5001")
ENRICHER_NAME = "email_to_intelligence"
SHEET_RANGE = "main!A:G"
HEADER_ROW_COUNT = 1


def gog_get(range_spec):
    cmd = ["gog", "sheets", "get", SPREADSHEET_ID, range_spec, "-j", "--results-only"]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print("ERROR: gog get failed: " + result.stderr)
        sys.exit(1)
    data = json.loads(result.stdout)
    return data.get("values", [])


def gog_update(range_spec, values):
    payload = json.dumps({"values": values})
    cmd = ["gog", "sheets", "update", SPREADSHEET_ID, range_spec, "--data-json", payload]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print("WARN: gog update failed: " + result.stderr)
    return result.returncode == 0


def launch_enricher(enricher_name, emails, sketch_id):
    payload = json.dumps({
        "node_ids": ["email:" + e for e in emails],
        "sketch_id": sketch_id or "default",
    }).encode()
    url = API_URL + "/api/enrichers/" + enricher_name + "/launch"
    req = urllib.request.Request(url, data=payload,
                                 headers={"Content-Type": "application/json"},
                                 method="POST")
    try:
        with urllib.request.urlopen(req) as resp:
            result = json.loads(resp.read())
            return result.get("id")
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        print("ERROR: Launch failed (" + str(e.code) + "): " + body)
        return None


def poll_task(task_id, timeout=120):
    start = time.time()
    url = API_URL + "/api/tasks/" + task_id
    while time.time() - start < timeout:
        try:
            req = urllib.request.Request(url)
            with urllib.request.urlopen(req) as resp:
                status = json.loads(resp.read())
            state = status.get("status", "PENDING")
            print("  Task state: " + state)
            if state in ("SUCCESS", "FAILURE"):
                return status
        except Exception as e:
            print("  Poll error: " + str(e))
        time.sleep(5)
    print("ERROR: Task timed out")
    return None


def extract_phone_and_linkedin(individual):
    phone = None
    linkedin = None
    phones = individual.get("phone_numbers")
    if phones and isinstance(phones, list) and len(phones) > 0:
        first = phones[0]
        if isinstance(first, dict):
            phone = first.get("number") or first.get("phone") or str(first)
        else:
            phone = str(first)
    social = individual.get("social_media_profiles")
    if social and isinstance(social, list):
        for s in social:
            if "linkedin.com" in str(s).lower():
                linkedin = str(s)
                break
    return phone, linkedin


def main():
    print("=== Enricher Integration: Peptide Buyers Sheet ===")
    print("API: " + API_URL)
    print("Enricher: " + ENRICHER_NAME)
    print("Sheet: " + SPREADSHEET_ID)
    print()
    print("[1] Reading emails from sheet...")
    rows = gog_get(SHEET_RANGE)
    if not rows:
        print("ERROR: No data in sheet")
        sys.exit(1)
    data_rows = rows[1:]
    print("  Found " + str(len(data_rows)) + " data rows")
    targets = []
    for i, row in enumerate(data_rows):
        email = row[0].strip() if len(row) > 0 and row[0] else ""
        phone = row[3].strip() if len(row) > 3 and row[3] else ""
        linkedin = row[6].strip() if len(row) > 6 and row[6] else ""
        if email and (not phone or not linkedin):
            targets.append((i, email, phone, linkedin))
    print("  Rows needing enrichment: " + str(len(targets)))
    if not targets:
        print("DONE: All rows already filled")
        return
    for row_idx, email, phone, linkedin in targets:
        print()
        print("  Processing: " + email)
        task_id = launch_enricher(ENRICHER_NAME, [email], None)
        if not task_id:
            print("  SKIP: Failed to launch enricher")
            continue
        print("  Task ID: " + task_id)
        result = poll_task(task_id)
        if not result or result.get("status") != "SUCCESS":
            print("  SKIP: Task failed")
            continue
        result_data = result.get("result", {})
        new_phone, new_linkedin = extract_phone_and_linkedin(result_data)
        sheet_row = HEADER_ROW_COUNT + row_idx + 1
        if not phone and new_phone:
            gog_update("main!D" + str(sheet_row), [[new_phone]])
            print("  Wrote D" + str(sheet_row) + " = " + new_phone)
        if not linkedin and new_linkedin:
            gog_update("main!G" + str(sheet_row), [[new_linkedin]])
            print("  Wrote G" + str(sheet_row) + " = " + new_linkedin)

if __name__ == "__main__":
    main()
