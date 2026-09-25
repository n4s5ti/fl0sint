#!/usr/bin/env python3
"""Query the FlowSint Datapoint Atlas. Usage: ./flowsint-datapoints.py [SEARCH]"""
import sys

import yaml

data = yaml.safe_load(open('/home/n4s5ti/.omp/datapoints.yaml'))
q = sys.argv[1].lower() if len(sys.argv) > 1 else ''

out = []
for dp in data['datapoints']:
    if q in dp['datapoint_id'].lower() or q in dp['category'].lower() or not q:
        out.append(f"{dp['datapoint_id']:45s} {dp['category']:15s} {dp['sensitivity']:8s} {dp['description']}")

print(f"Datapoints matching '{q or '(all)'}': {len(out)}")
print(f"{'ID':45s} {'Category':15s} {'Sensitivity':8s} Description")
print('-' * 120)
print('\n'.join(out))
