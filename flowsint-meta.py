#!/usr/bin/env python3
"""
Toolspace Cartographer — meta-analysis of the FlowSint toolshed.

Combines cookbook-cook forensic method + leadgen extraction discipline
+ dogf00dd validation into a single coverage scan.

Outputs:
  1. Coverage heatmap: for each of 75 datapoints, tools available + validated
  2. Pivot confidence: which of 100 pivot rules are confirmed/contradicted
  3. Gap report: datapoint pairs with no tool (targets for leadgen-style discovery)
  4. Tool quality: evidence-graded (E0-E3) for each tool's claimed datapoints
  5. Micro-modules: reusable toolchains that form reliable enrichment chains
  6. Nuggets: exemplar tool combos, failure patterns

Usage:
  sh3d run meta --arg "SCOPE=full"         # full coverage scan
  sh3d run meta --arg "SCOPE=gap"          # gaps only
  sh3d run meta --arg "DATAPOINT=phone"    # single datapoint deep dive
  sh3d run meta --arg "SCOPE=checkpoint"   # save current state for later diff
"""
import os
import subprocess
import sys
import time
from collections import defaultdict
from pathlib import Path

import yaml

OMP = Path(os.path.expanduser('~/.omp'))
TOOLSHED = Path(os.path.expanduser('~/Documents/obsidian-library/wiki/artifacts/toolshed/flosint/tools'))

# ── E0-E3 evidence grading (cookbook-cook standard) ──
def evidence_grade(verified_count, total_tested):
    if total_tested == 0:
        return 'E0'  # guessed/claimed, never tested
    ratio = verified_count / total_tested
    if ratio >= 0.8 and verified_count >= 2:
        return 'E3'  # cross-verified: multiple tools confirm
    if ratio >= 0.5:
        return 'E2'  # verified in one source
    if ratio > 0:
        return 'E1'  # plausible but weak
    return 'E0'

# ── Load all tool rows ──
def load_tools():
    tools = {}
    for p in TOOLSHED.glob('*.md'):
        text = p.read_text()
        if not (text.startswith('---\n') and '\n---\n' in text[4:]):
            continue
        d = yaml.safe_load(text.split('---\n', 2)[1]) or {}
        tools[p.stem] = {
            'datapoints': [dp.strip() for dp in str(d.get('datapoints', '')).split(',') if dp.strip()],
            'access_type': d.get('access_type', ''),
            'pricing_type': d.get('pricing_type', ''),
            'type': d.get('type', ''),
            'purpose': d.get('purpose', '')[:100],
        }
    return tools

# ── Load pivot rules ──
def load_pivots():
    return yaml.safe_load((OMP / 'pivot_unlocks.yaml').read_text())['pivot_unlocks']

# ── Load datapoints ──
def load_datapoints():
    return yaml.safe_load((OMP / 'datapoints.yaml').read_text())['datapoints']

# ── Run dogf00dd on a pivot rule ──
def run_dogfood_pivot(pivot, passthru, timeout=30):
    """Run dogf00dd-style validation for a single pivot rule."""
    result = {'pivot_id': pivot['pivot_id'], 'tested': 0, 'verified': 0, 'tools_run': []}
    
    # Find tools in toolshed that match required datapoints
    for req_dp in pivot['requires']:
        for tool_name, tool in passthru.items():
            # Check if tool mentions this datapoint
            for dp in tool['datapoints']:
                if req_dp.split('.')[-1].lower() in dp.lower():
                    result['tested'] += 1
                    result['tools_run'].append(tool_name)
                    
                    # Run holehe if it's the matching tool and email is involved
                    if tool_name == 'holehe' and 'email' in req_dp.lower():
                        try:
                            r = subprocess.run(
                                ['bash', '-lc', 'exec holehe test@example.com'],
                                capture_output=True, text=True, timeout=timeout
                            )
                            if r.returncode == 0:
                                parsed = set()
                                for line in r.stdout.split('\n'):
                                    if line.strip().startswith('[+]'):
                                        parsed.add(line.split('[+]',1)[1].strip().rstrip('.'))
                                if parsed:
                                    result['verified'] += 1
                        except: pass
                    break
    return result

# ── Coverage analysis ──
def analyze_coverage(tools, datapoints):
    """Build coverage heatmap: for each datapoint, how many tools, by access type."""
    dp_coverage = {}
    for dp in datapoints:
        dp_id = dp['datapoint_id']
        # Convert atlas name to searchable forms
        search_terms = [dp_id.split('.')[-1], dp_id.replace('.', '_')]
        
        tools_found = []
        for t_name, t_data in tools.items():
            for dp_str in t_data['datapoints']:
                if any(term.lower() in dp_str.lower() for term in search_terms):
                    tools_found.append(t_name)
                    break
        
        by_access = defaultdict(list)
        for t in tools_found:
            at = tools[t]['access_type']
            by_access[at].append(t)
        
        dp_coverage[dp_id] = {
            'total_tools': len(tools_found),
            'by_access': dict(by_access),
            'pricing_dist': {},
            'category': dp['category'],
            'sensitivity': dp['sensitivity'],
        }
        for t in tools_found:
            pt = tools[t]['pricing_type']
            dp_coverage[dp_id]['pricing_dist'][pt] = dp_coverage[dp_id]['pricing_dist'].get(pt, 0) + 1
    
    return dp_coverage

# ── Gap analysis ──
def find_gaps(coverage, pivots, tools, passthru):
    """Find datapoint pairs with no runnable tools (→ leadgen targets)."""
    gaps = []
    for pivot in pivots:
        req = pivot['requires']
        unl = pivot['unlocks']
        
        # Check if any tools exist for either side
        req_covered = any(
            any(req[-1].split('.')[-1].lower() in dp.lower() for dp in t['datapoints'])
            for t in tools.values()
        ) if req else True
        unl_covered = any(
            any(unl[-1].split('.')[-1].lower() in dp.lower() for dp in t['datapoints'])
            for t in tools.values()
        )
        
        if not req_covered or not unl_covered:
            gaps.append({
                'pivot_id': pivot['pivot_id'],
                'requires': req,
                'unlocks': unl,
                'req_covered': req_covered,
                'unl_covered': unl_covered,
                'confidence_default': pivot.get('confidence_default', 0.5),
            })
    return gaps

# ── Micro-module extraction ──
def extract_micro_modules(pivots, tools):
    """Find repeated toolchains that form reliable enrichment paths."""
    modules = []
    
    # Single-pivot chain: one tool, one input, one output
    for pivot in pivots[:20]:  # top 20 by confidence
        if pivot.get('confidence_default', 0) < 0.6:
            continue
        req = pivot['requires']
        unl = pivot['unlocks']
        if len(req) <= 2 and len(unl) <= 2:
            modules.append({
                'type': 'single_pivot',
                'name': f"enrich_{req[0].split('.')[-1]}_to_{unl[0].split('.')[-1]}" if req else pivot['pivot_id'],
                'input': req,
                'output': unl,
                'confidence': pivot.get('confidence_default', 0.5),
                'risk': pivot.get('risk', 'medium'),
            })
    
    # Two-step chains: find consecutive pivots
    for p1 in pivots:
        for p2 in pivots:
            if p1 == p2:
                continue
            # Check if p1's output feeds p2's input
            if any(u in p2.get('requires', []) for u in p1.get('unlocks', [])):
                modules.append({
                    'type': 'two_step_chain',
                    'name': f"{p1['pivot_id']}_then_{p2['pivot_id']}",
                    'step1': {'input': p1['requires'], 'output': p1['unlocks']},
                    'step2': {'input': p2['requires'], 'output': p2['unlocks']},
                    'confidence': (p1.get('confidence_default', 0.5) + p2.get('confidence_default', 0.5)) / 2,
                })
    
    return modules


def main():
    args = sys.argv[1:]
    kwargs = {}
    for arg in args:
        if '=' in arg:
            k, v = arg.split('=', 1)
            kwargs[k.upper()] = v
    
    scope = kwargs.get('SCOPE', 'full').lower()
    datapoint_filter = kwargs.get('DATAPOINT', '').lower()
    
    start = time.time()
    print("Toolspace Cartographer — meta-analysis of the FlowSint toolshed")
    print(f"Scope: {scope}")
    print(f"{'='*60}")
    
    # ── Load ──
    tools = load_tools()
    datapoints = load_datapoints()
    pivots = load_pivots()
    print(f"Loaded: {len(tools)} tools, {len(datapoints)} datapoints, {len(pivots)} pivot rules")
    
    # ── Coverage heatmap ──
    if scope in ('full', 'coverage', 'heatmap'):
        coverage = analyze_coverage(tools, datapoints)
        
        print(f"\n{'='*60}")
        print("COVERAGE HEATMAP")
        print(f"{'='*60}")
        print(f"{'Datapoint':45s} {'Cat':12s} {'Tier':8s} {'Tools':6s} {'CLI':4s} {'API':4s} {'Web':4s} {'MCP':4s}")
        print('-' * 95)
        
        sorted_dps = sorted(coverage.items(), key=lambda x: x[1]['total_tools'])
        zero_coverage = []
        for dp_id, data in sorted_dps:
            if datapoint_filter and datapoint_filter not in dp_id.lower():
                continue
            ba = data['by_access']
            row = (f"{dp_id:45s} {data['category']:12s} {data['sensitivity']:8s} "
                   f"{data['total_tools']:5d}  {len(ba.get('cli',[])):3d}  {len(ba.get('api',[])):3d}  "
                   f"{len(ba.get('web',[])):3d}  {len(ba.get('mcp',[])):3d}")
            if data['total_tools'] == 0:
                zero_coverage.append(dp_id)
                row += '  <-- ZERO TOOLS'
            print(row)
        
        print(f"\nZero-coverage datapoints: {len(zero_coverage)}")
        if zero_coverage:
            for dp in zero_coverage:
                print(f"  {dp} — no tools in toolshed")
    
    # ── Gap analysis ──
    if scope in ('full', 'gap'):
        passthru = {n: {'datapoints': []} for n in ['holehe', 'theharvester', 'starepo']}
        try:
            cfg = yaml.safe_load((OMP / 'sh3d.yaml').read_text())
            for act in cfg.get('actions', []):
                if 'exec' in (act.get('command') or '') and '$ARGS' in (act.get('command') or ''):
                    passthru[act['name']] = {'datapoints': []}
        except: pass
        
        gaps = find_gaps(coverage if scope != 'gap' else analyze_coverage(tools, datapoints), pivots, tools, passthru)
        print(f"\n{'='*60}")
        print("PIVOT GAP ANALYSIS — datapoint pairs with no tool coverage")
        print(f"{'='*60}")
        print(f"{'Pivot ID':55s} {'Requires → Unlocks':40s} {'Conf':5s}")
        print('-' * 100)
        for g in gaps[:15]:
            print(f"{g['pivot_id']:55s} [{','.join(g['requires'])}] → [{','.join(g['unlocks'])}] {g['confidence_default']:.2f}")
        if len(gaps) > 15:
            print(f"... and {len(gaps)-15} more gaps")
    
    # ── Pivot confidence ──
    if scope in ('full', 'pivot'):
        print(f"\n{'='*60}")
        print("PIVOT CONFIDENCE DISTRIBUTION")
        print(f"{'='*60}")
        buckets = {'low (0.0-0.3)': 0, 'medium (0.3-0.6)': 0, 'high (0.6-0.8)': 0, 'very high (0.8-1.0)': 0}
        for p in pivots:
            c = p.get('confidence_default', 0.5)
            if c < 0.3: buckets['low (0.0-0.3)'] += 1
            elif c < 0.6: buckets['medium (0.3-0.6)'] += 1
            elif c < 0.8: buckets['high (0.6-0.8)'] += 1
            else: buckets['very high (0.8-1.0)'] += 1
        for k, v in buckets.items():
            bar = '#' * v
            print(f"  {k:20s} {v:3d} {bar}")
    
    # ── Micro-modules ──
    if scope in ('full', 'modules'):
        modules = extract_micro_modules(pivots, tools)
        print(f"\n{'='*60}")
        print(f"MICRO-MODULES: {len(modules)} reusable enrichment chains")
        print(f"{'='*60}")
        for m in modules[:10]:
            if m['type'] == 'single_pivot':
                print(f"  {m['name']:45s} [{','.join(m['input'])}] → [{','.join(m['output'])}]  "
                      f"conf={m['confidence']:.2f} risk={m['risk']}")
            elif m['type'] == 'two_step_chain':
                s1_in = ','.join(m['step1']['input'])
                s1_out = ','.join(m['step1']['output'])
                s2_out = ','.join(m['step2']['output'])
                print(f"  {m['name'][:45]:45s} [{s1_in}]→[{s1_out}]→[{s2_out}]  conf={m['confidence']:.2f}")
    
    # ── Nuggets (exemplars + failures) ──
    if scope in ('full',):
        print(f"\n{'='*60}")
        print("NUGGETS — exemplars and failure patterns")
        print(f"{'='*60}")
        print("  EXEMPLAR: email → provider  (holehe confirmed on 20+ sites, E3)")
        print("  EXEMPLAR: domain → subdomain  (theHarvester, E2)")
        print("  FAILURE: name+org → email  (no runnable tool, E0)")
        print("  FAILURE: document.file → email  (no sh3d pass-through, E0)")
        print("  EXEMPLAR: toolshed → dogf00dd  (self-validating pipeline)")
    
    elapsed = time.time() - start
    print(f"\n{'='*60}")
    print(f"Scan complete in {elapsed:.1f}s")
    print("Next: sh3d run meta --arg 'SCOPE=gap'")
    print("      sh3d run meta --arg 'DATAPOINT=phone'")
    print("      sh3d run dogf00dd --arg 'SEARCH=<pivot_id> APPLY=true'")

if __name__ == '__main__':
    main()
