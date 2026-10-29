"""Replay observed market snapshots through the paper-only meme dip strategy."""
import argparse
from dataclasses import asdict
import json
from pathlib import Path
from meme_dip import DipRules, PaperDipTrader


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('snapshots', type=Path, help='Chronological JSONL; full input contract in MEME_TRADER.md')
    parser.add_argument('--balance', type=float, default=100, help='Virtual dollars only')
    parser.add_argument('--rules', type=Path, help='Optional JSON overrides for provisional paper settings')
    parser.add_argument('--output', type=Path, default=Path('state/paper_memes/report.json'))
    args = parser.parse_args()
    rules = DipRules(**json.loads(args.rules.read_text())) if args.rules else DipRules()
    trader = PaperDipTrader(args.balance, rules)
    events = []
    with args.snapshots.open() as source:
        for index, line in enumerate(source, 1):
            if not line.strip():
                continue
            try:
                snapshot = json.loads(line)
                event = trader.on_snapshot(snapshot)
            except (KeyError, ValueError, TypeError) as error:
                raise ValueError('Invalid paper snapshot at line ' + str(index)) from error
            events.append({'observed_at': snapshot['observed_at'], 'mint': snapshot['mint'], **event})
    report = {'mode': 'paper_only', 'starting_virtual_usd': args.balance, 'cash_usd': trader.cash,
              'open_position': trader.position, 'drawdown_halted': trader.halted,
              'rules': asdict(rules), 'events': events,
              'limitations': 'Sampled quote fills with assumed costs. Supplied evidence is not independently verified by replay. No wallet access or transactions.'}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_suffix('.tmp')
    temporary.write_text(json.dumps(report, indent=2) + '\n')
    temporary.replace(args.output)
    print(json.dumps({'mode': 'paper_only', 'snapshots': len(events), 'report': str(args.output)}))


if __name__ == '__main__':
    main()
