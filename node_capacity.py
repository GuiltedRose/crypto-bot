"""UNTESTED offline mainnet capacity assessment; never probes or starts a node."""
import argparse
import json
import math
from pathlib import Path

REQUIREMENTS_URL = 'https://docs.anza.xyz/operations/requirements'


def assess(inventory):
    """Compare declared inventory to guidance, not a measured viability proof."""
    blockers = []
    unknown = []
    thresholds = {'ram_gb': 256, 'accounts_nvme_gb': 1000,
                  'ledger_nvme_gb': 1000, 'snapshots_nvme_gb': 500,
                  'cpu_base_ghz': 2.8, 'symmetric_network_gbps': 1}
    for name, minimum in thresholds.items():
        value = inventory.get(name)
        if value is None:
            unknown.append(name)
        elif type(value) not in (int, float) or not math.isfinite(value) or value < 0:
            raise ValueError('Invalid capacity: ' + name)
        elif value < minimum:
            blockers.append({'field': name, 'declared': value, 'guidance': minimum})
    # Free archive space is not evidence of disk topology, NVMe performance or TBW.
    free = inventory.get('total_free_gb')
    if free is not None:
        if type(free) not in (int, float) or not math.isfinite(free) or free < 0:
            raise ValueError('Invalid free capacity')
        if free < 2500:
            blockers.append({'field': 'total_free_gb', 'declared': free,
                             'guidance': 2500, 'scope': 'accounts + ledger + snapshots guidance'})
    for name in ('avx2', 'sha_extensions', 'separate_accounts_ledger_nvme', 'public_ipv4'):
        value = inventory.get(name)
        if value is None:
            unknown.append(name)
        elif type(value) is not bool:
            raise ValueError('Expected boolean: ' + name)
        elif not value:
            blockers.append({'field': name, 'declared': False, 'guidance': True})
    return {'assessment': 'NO_GO_CURRENT_HARDWARE' if blockers else 'UNPROVEN',
            'basis': 'declared inventory and official recommendations; not a benchmark',
            'source': REQUIREMENTS_URL, 'review_date': '2026-10-30',
            'blockers': blockers, 'unknown': unknown, 'node_started': False,
            'independent_execution_implemented': False,
            'note': 'Passing this comparison never proves mainnet throughput or finalization correctness.'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('inventory', type=Path)
    args = parser.parse_args()
    print(json.dumps(assess(json.loads(args.inventory.read_text())), indent=2))


if __name__ == '__main__':
    main()
