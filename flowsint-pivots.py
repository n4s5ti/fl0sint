#!/usr/bin/env python3
"""Query the FlowSint Pivot Unlock Graph. Usage: ./flowsint-pivots.py [SEARCH]"""
import sys

import yaml

data = yaml.safe_load(open('/home/n4s5ti/.omp/pivot_unlocks.yaml'))
q = sys.argv[1].lower() if len(sys.argv) > 1 else ''

out = []
for p in data['pivot_unlocks']:
    req = ', '.join(p['requires'])
    unl = ', '.join(p['unlocks'])
    if q in req.lower() or q in unl.lower() or not q:
        out.append(f"{p['pivot_id']:60s} [{req}] → [{unl}]")

print(f"Pivot rules matching '{q or '(all)'}': {len(out)}")
print(f"{'Pivot ID':60s} Rule")
print('-' * 120)
print('\n'.join(out))
