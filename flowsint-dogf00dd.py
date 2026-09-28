#!/usr/bin/env python3
"""
sh3d dogf00dd — self-referential enrichment: run tools from the toolshed against
the pivot graph to discover real (confirmed/contradicted) pivot unlocks.

Usage:
  sh3d run dogf00dd --arg "SEARCH=email"
  sh3d run dogf00dd --arg "SEARCH=phone APPLY=true"
"""

import os
import subprocess
import sys
from pathlib import Path

import yaml

OMP = Path(os.path.expanduser('~/.omp'))
TOOLSHED = Path(os.path.expanduser('~/Documents/obsidian-library/wiki/artifacts/toolshed/flosint/tools'))

ATLAS_TO_TOOLSHED = {
    'contact.email.address': 'email_address',
    'contact.email.validated': 'email_valid',
    'contact.email.domain': 'domain',
    'contact.phone.e164': 'phone',
    'contact.phone.carrier': 'carrier',
    'contact.address.postal': 'address',
    'contact.address.city': 'city',
    'contact.address.region': 'state',
    'contact.address.postal_code': 'zip',
    'person.name.first': 'first_name',
    'person.name.last': 'last_name',
    'person.age.estimated': 'estimated_age',
    'person.birth_date': 'birth_date',
    'account.username': 'username',
    'account.profile_url': 'social_profile',
    'account.facebook.profile_url': 'facebook',
    'account.instagram.profile_url': 'instagram',
    'account.linkedin.profile_url': 'linkedin',
    'account.facebook.id': 'facebook_uuid',
    'org.name': 'organization',
    'org.employee_name_candidate': 'employment',
    'net.domain.name': 'domain',
    'net.ip.address': 'ip_address',
    'breach.data': 'breach_data',
}

# Tool-specific output parsers
def _parse_holehe(stdout):
    """Return sites where [+] indicates email is registered."""
    found = set()
    for line in stdout.split('\n'):
        line = line.strip()
        if line.startswith('[+]'):
            site = line.split('[+]', 1)[1].strip().rstrip('.')
            if site:
                found.add(site)
    return found


def _parse_theharvester(stdout):
    """Return emails found in theHarvester output."""
    import re
    emails = re.findall(r'[\w.+-]+@[\w-]+\.[\w.]+', stdout)
    return set(emails)


PARSERS = {
    'holehe': _parse_holehe,
    'theharvester': _parse_theharvester,
}

# Shell commands for runnable tools
TOOL_COMMANDS = {
    'holehe': 'holehe',
    'theharvester': 'theHarvester',
    'starepo': 'starepo',
}


def atlas_names_to_toolshed(atlas_names):
    result = set()
    for name in atlas_names:
        mapped = ATLAS_TO_TOOLSHED.get(name)
        if mapped:
            result.add(mapped)
        else:
            parts = name.split('.')
            result.add(parts[-1])
    return list(result)


def discover_passthrough_tools():
    pt = {}
    try:
        cfg = yaml.safe_load((OMP / 'sh3d.yaml').read_text())
        for action in cfg.get('actions', []):
            name = action.get('name', '')
            cmd = action.get('command', '')
            if 'exec' in cmd and '$ARGS' in cmd:
                tool_file = TOOLSHED / f'{name}.md'
                dps = []
                if tool_file.exists():
                    text = tool_file.read_text()
                    if text.startswith('---\n') and '\n---\n' in text[4:]:
                        d = yaml.safe_load(text.split('---\n', 2)[1]) or {}
                        dps = [dp.strip() for dp in str(d.get('datapoints', '')).split(',') if dp.strip()]
                pt[name] = {'datapoints': dps}
        return pt
    except Exception:
        return {}


def search_toolshed(datapoints):
    results = []
    for p in sorted(TOOLSHED.glob('*.md')):
        text = p.read_text()
        if not (text.startswith('---\n') and '\n---\n' in text[4:]):
            continue
        d = yaml.safe_load(text.split('---\n', 2)[1]) or {}
        tool_dps = str(d.get('datapoints', '')).lower().replace('_', '')
        if d.get('type') in ('prompt',):
            continue
        for dp in datapoints:
            clean_dp = dp.lower().replace('_', '').replace('.', '')
            if clean_dp in tool_dps:
                results.append({
                    'tool': p.stem,
                    'datapoints': d.get('datapoints', ''),
                    'access_type': d.get('access_type', ''),
                    'pricing_type': d.get('pricing_type', ''),
                    'input': d.get('input', ''),
                })
                break
    return results


def search_pivots(pivots_data, search, from_dp='', to_dp=''):
    q = search.lower()
    matches = []
    for p in pivots_data:
        pid = p['pivot_id'].lower()
        req = ','.join(p['requires']).lower()
        unl = ','.join(p['unlocks']).lower()
        if q in pid or q in req or q in unl:
            if from_dp and from_dp.lower() not in req:
                continue
            if to_dp and to_dp.lower() not in unl:
                continue
            matches.append(p)
    return matches


def load_pivots():
    return yaml.safe_load((OMP / 'pivot_unlocks.yaml').read_text())['pivot_unlocks']


def infer_test_input(requires):
    req_str = ' '.join(requires).lower()
    if 'email' in req_str:
        return 'test@example.com'
    if 'phone' in req_str:
        return '+14155551234'
    if 'domain' in req_str or 'dns' in req_str or 'url' in req_str:
        return 'example.com'
    if 'username' in req_str:
        return 'testuser'
    if 'name' in req_str:
        return 'John+Doe'
    return ''


def run_tool(tool_name, test_input):
    result = {'tool': tool_name, 'input': test_input, 'status': 'unknown'}
    binary = TOOL_COMMANDS.get(tool_name)
    if not binary:
        result['status'] = 'unsupported'
        return result
    cmd = f'{binary} {test_input}'
    try:
        r = subprocess.run(['bash', '-lc', f'exec {cmd}'],
                          capture_output=True, text=True, timeout=30)
        result['exit_code'] = r.returncode
        result['stdout'] = r.stdout[:3000]
        result['stderr'] = r.stderr[:500]
        result['status'] = 'success' if r.returncode == 0 else 'failed'
    except subprocess.TimeoutExpired:
        result['status'] = 'timeout'
    except FileNotFoundError:
        result['status'] = 'not_installed'
    return result


def evaluate_pivot(pivot, tool_results):
    signal = {
        'pivot_id': pivot['pivot_id'],
        'confirmed': False,
        'contradicted': False,
        'confidence': pivot.get('confidence_default', 0.5),
        'methods_tested': [],
        'hits': 0,
        'misses': 0,
    }
    for r in tool_results:
        if r['status'] != 'success':
            signal['methods_tested'].append(f"{r['tool']}: {r['status']}")
            continue

        stdout = r.get('stdout', '')
        parsed = PARSERS.get(r['tool'], lambda s: set())(stdout)
        tool_name = r['tool']

        for unlock in pivot.get('unlocks', []):
            hit = False
            reason = ''

            if tool_name == 'holehe' and parsed:
                # holehe: [+] site means email exists on that provider
                if 'email' in unlock.lower() or 'provider' in unlock.lower():
                    hit = True
                    reason = f" [+] on {len(parsed)} sites"
                elif any(p in stdout.lower() for p in unlock.split('.')):
                    hit = True
                    reason = ' found in output'
                # Also check: any term matches a [+] site name
                for site in parsed:
                    site_lower = site.lower()
                    for unlock_piece in unlock.split('.'):
                        if unlock_piece.lower() in site_lower:
                            hit = True
                            reason = f" matched {site} in [+] output"
                            break
                    if hit:
                        break
            elif tool_name == 'theharvester' and parsed:
                if 'email' in unlock.lower():
                    hit = True
                    reason = f" {len(parsed)} emails found"
                for unlock_piece in unlock.split('.'):
                    for email in parsed:
                        if unlock_piece.lower() in email.lower():
                            hit = True
                            reason = f" matched in {email[:30]}"
                            break
                    if hit:
                        break
            else:
                term = unlock.split('.')[-1]
                if term.lower() in stdout.lower():
                    hit = True
                    reason = ' found in stdout'

            if hit:
                signal['hits'] += 1
                signal['methods_tested'].append(f"  {tool_name}: {unlock} CONFIRMED{reason}")
            else:
                signal['misses'] += 1
                signal['methods_tested'].append(f"  {tool_name}: {unlock} NOT_FOUND")

    if signal['hits'] > 0:
        signal['confirmed'] = True
        signal['confidence'] = min(0.95, pivot.get('confidence_default', 0.5) + 0.2 * signal['hits'])
    elif signal['misses'] > 0:
        signal['contradicted'] = True
        signal['confidence'] = max(0.1, pivot.get('confidence_default', 0.5) - 0.15)
    return signal


def main():
    args = sys.argv[1:]
    kwargs = {}
    for arg in args:
        if '=' in arg:
            k, v = arg.split('=', 1)
            kwargs[k.upper()] = v

    search = kwargs.get('SEARCH', ' '.join(args) if args else '')
    from_dp = kwargs.get('FROM', '')
    to_dp = kwargs.get('UNLOCKS', '')
    apply = kwargs.get('APPLY', '').lower() in ('true', '1', 'yes')
    limit = int(kwargs.get('LIMIT', 5))

    if not search:
        print('Usage: sh3d run dogf00dd --arg "SEARCH=email"')
        sys.exit(1)

    pivots = load_pivots()
    passthru = discover_passthrough_tools()
    matches = search_pivots(pivots, search, from_dp, to_dp)

    print(f'dogf00dd: searching for "{search}"')
    print(f'  {len(pivots)} pivot rules, {len(matches)} matching')
    print(f'  {len(passthru)} pass-through tools in sh3d')

    total_confirmed = 0
    total_contradicted = 0

    for pivot in matches[:limit]:
        reqs = ', '.join(pivot['requires'])
        unls = ', '.join(pivot['unlocks'])
        print(f'\n>> Pivot: {pivot["pivot_id"]}')
        print(f'   Requires: [{reqs}]')
        print(f'   Unlocks:  [{unls}]')

        td_names = atlas_names_to_toolshed(pivot['requires'] + pivot['unlocks'])
        print(f'   Toolshed names: {td_names}')

        tools = search_toolshed(td_names)
        print(f'   Tools in toolshed: {len(tools)}')

        runnable = [t for t in tools if t['tool'] in passthru]
        print(f'   Runnable via sh3d: {len(runnable)}')
        if runnable:
            for t in runnable:
                print(f'      {t["tool"]}: {t["datapoints"][:60]}')

        if not runnable:
            print('   No runnable tools, skipping')
            continue

        results = []
        for t in runnable[:2]:
            test_input = infer_test_input(pivot['requires'])
            if not test_input:
                print(f'   Skip {t["tool"]}: no test input')
                continue
            print(f'   >> Running {t["tool"]} {test_input}...')
            result = run_tool(t['tool'], test_input)
            results.append(result)
            print(f'      Exit {result.get("exit_code","?")}: {result["status"]}')

        signal = evaluate_pivot(pivot, results)
        if signal['confirmed']:
            total_confirmed += 1
            print(f'   ** CONFIRMED  confidence={signal["confidence"]:.2f}')
        elif signal['contradicted']:
            total_contradicted += 1
            print(f'   ** CONTRADICTED  confidence={signal["confidence"]:.2f}')
        else:
            print(f'   ** INCONCLUSIVE  confidence={signal["confidence"]:.2f}')
        for m in signal['methods_tested']:
            print(m)

    print(f'\ndogf00dd: {len(matches[:limit])} examined')
    print(f'  Confirmed: {total_confirmed}')
    print(f'  Contradicted: {total_contradicted}')

    if apply:
        updated = 0
        for pivot in pivots:
            if any(p['pivot_id'] == pivot['pivot_id'] for p in matches):
                td_names = atlas_names_to_toolshed(pivot['requires'] + pivot['unlocks'])
                tools = [t for t in search_toolshed(td_names) if t['tool'] in passthru]
                results = []
                for t in tools[:2]:
                    inp = infer_test_input(pivot['requires'])
                    if inp:
                        results.append(run_tool(t['tool'], inp))
                signal = evaluate_pivot(pivot, results)
                if signal['confirmed']:
                    pivot['confidence_default'] = signal['confidence']
                    pivot['requires_verification'] = signal['confidence'] < 0.7
                    updated += 1
        (OMP / 'pivot_unlocks.yaml').write_text(
            yaml.dump({'version': 1, 'pivot_unlocks': pivots},
                      default_flow_style=False, sort_keys=False))
        print(f'  Updated {updated} pivot rules')


if __name__ == '__main__':
    main()
