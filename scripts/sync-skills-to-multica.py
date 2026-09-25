#!/usr/bin/env python3
"""
Sync local OMP skills (user + managed) into multica workspace with upsert.

Scans ~/.omp/agent/skills/ and ~/.omp/agent/managed-skills/ for SKILL.md
bundles. Creates missing skills, updates changed ones, and upserts
additional files.

Uses SHA256 content hash to skip updates for unchanged SKILL.md.
Supports --quiet for watcher mode (suppresses expected 409 noise).

Usage:
  ./scripts/sync-skills-to-multica.py                # normal run
  ./scripts/sync-skills-to-multica.py --dry-run      # preview
  ./scripts/sync-skills-to-multica.py --quiet        # watcher/background mode
"""

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys

import yaml

def normalize_text(text: str) -> str:
    """Normalize text for consistent checksum comparison.
    Strips trailing whitespace lines to handle multica's trimming behavior."""
    return text.rstrip("\n\r\t ") + "\n"

OMP_SKILL_DIRS = [
    os.path.expanduser("~/.omp/agent/skills"),
    os.path.expanduser("~/.omp/agent/managed-skills"),
]

MULTICA_WORKSPACE = "a891648f-b3d2-4144-a13c-c58b720e3ca0"
os.environ.setdefault("MULTICA_WORKSPACE_ID", MULTICA_WORKSPACE)


def file_sha256(path: str) -> str:
    """Return lowercase hex SHA256 of file contents."""
    h = hashlib.sha256()
    with open(path) as f:
        h.update(normalize_text(f.read()).encode())
    return h.hexdigest()


def run_multica(profile, args, dry_run=False, quiet=False):
    """Run multica CLI with profile and workspace flags."""
    cmd = ["multica", "--profile", profile] + args
    if dry_run:
        print(f"[dry-run] {' '.join(cmd)}")
        return None
    env = {**os.environ, "MULTICA_WORKSPACE_ID": MULTICA_WORKSPACE}
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=30, env=env)
    if result.returncode != 0:
        err = result.stderr.strip()[:300]
        # 409 "already exists" is expected during create-only phase; suppress in quiet mode
        if quiet and "already exists" in err:
            return None
        print(f"[warn] multica error: {err}", file=sys.stderr)
        return None
    try:
        return json.loads(result.stdout) if result.stdout.strip() else {}
    except json.JSONDecodeError:
        return result.stdout.strip()


def parse_skill_md(path):
    """Extract name and description from a SKILL.md frontmatter."""
    content = open(path).read()
    m = re.match(r"^---\s*\n(.*?)\n---", content, re.DOTALL)
    if m:
        try:
            meta = yaml.safe_load(m.group(1))
            name = meta.get("name", os.path.basename(os.path.dirname(path)))
            desc = meta.get("description", "")[:500]
            return name, desc, content
        except yaml.YAMLError:
            pass
    name = os.path.basename(os.path.dirname(path))
    first_line = content.strip().split("\n")[0][:80]
    return name, first_line, content


def get_existing_skills(profile):
    """Return dict of {skill_name: skill_id} from multica."""
    result = run_multica(profile, [
        "skill", "list",
        "--workspace-id", MULTICA_WORKSPACE,
        "--output", "json",
    ])
    if result is None:
        return {}
    skills = {}
    data = result if isinstance(result, list) else []
    for s in data:
        name = s.get("name", "")
        sid = s.get("id", "")
        if name and sid:
            skills[name] = sid
    return skills


def get_skill_checksum(profile, skill_id):
    """Get skill content checksum from the skill object."""
    result = run_multica(profile, [
        "skill", "get", skill_id,
        "--output", "json",
    ])
    if isinstance(result, dict):
        content = result.get("content", "")
        return hashlib.sha256(normalize_text(content).encode()).hexdigest()
    return None


def sync_skill_folder(skill_dir, profile, dry_run=False, quiet=False):
    """Sync a single skill folder into multica with upsert."""
    skill_name = os.path.basename(skill_dir)
    skill_path = os.path.join(skill_dir, "SKILL.md")

    if not os.path.isfile(skill_path):
        if not quiet:
            print(f"[skip] {skill_name}: no SKILL.md found")
        return False

    name, desc, _ = parse_skill_md(skill_path)
    if not name:
        name = skill_name

    existing = get_existing_skills(profile)
    local_hash = file_sha256(skill_path)

    if name in existing:
        skill_id = existing[name]
        remote_hash = get_skill_checksum(profile, skill_id)

        if remote_hash == local_hash:
            if not quiet:
                print(f"[unchanged] {name} — content matches, skipping")
        else:
            if not quiet:
                print(f"[update] {name} — content changed, updating...")
            run_multica(profile, [
                "skill", "update", skill_id,
                "--content-file", skill_path,
                "--output", "json",
            ], dry_run=dry_run)
            if not quiet:
                print(f"  -> Updated {name}")
    else:
        if not quiet:
            print(f"[create] {name} — pushing to multica...")
        result = run_multica(profile, [
            "skill", "create",
            "--workspace-id", MULTICA_WORKSPACE,
            "--name", name,
            "--description", desc,
            "--content-file", skill_path,
        ], dry_run=dry_run, quiet=quiet)
        if result is None and not dry_run:
            if not quiet:
                print(f"[error] failed to create {name}")
            return False
        if not dry_run:
            skill_id = result.get("id") if isinstance(result, dict) else None
            if skill_id and not quiet:
                print(f"  -> Created {name} (id: {skill_id})")
            elif skill_id:
                pass
            else:
                skill_id = None

    # Upsert sub-files
    if not dry_run and skill_id:
        for root, _dirs, files in os.walk(skill_dir):
            for fname in sorted(files):
                fpath = os.path.join(root, fname)
                rel_path = os.path.relpath(fpath, skill_dir)
                if rel_path == "SKILL.md":
                    continue
                if not quiet:
                    print(f"  [file] {rel_path}...")
                run_multica(profile, [
                    "skill", "files", "upsert", skill_id,
                    "--path", rel_path,
                    "--content-file", fpath,
                ])
    return True


def main():
    parser = argparse.ArgumentParser(description="Sync OMP skills to multica (upsert)")
    parser.add_argument("--dry-run", action="store_true", help="Print commands without executing")
    parser.add_argument("--quiet", action="store_true", help="Suppress expected messages (for watcher mode)")
    parser.add_argument("--profile", default="desktop-api.multica.ai",
                        help="Multica profile (default desktop-api.multica.ai)")
    args = parser.parse_args()
    pf = args.profile

    if args.dry_run:
        print("=== DRY RUN — no changes will be made ===")

    total = 0
    for base_dir in OMP_SKILL_DIRS:
        if not os.path.isdir(base_dir):
            print(f"[skip] {base_dir} not found")
            continue
        for entry in sorted(os.listdir(base_dir)):
            skill_dir = os.path.join(base_dir, entry)
            if not os.path.isdir(skill_dir):
                continue
            if sync_skill_folder(skill_dir, profile=pf, dry_run=args.dry_run, quiet=args.quiet):
                total += 1

    if not args.quiet:
        print(f"\nSynced {total} skills.")


if __name__ == "__main__":
    main()
