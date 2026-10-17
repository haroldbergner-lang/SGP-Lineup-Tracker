"""Run with python -m src.snapshots; CSV files are written incrementally."""
import argparse
import csv
from datetime import datetime, timezone
from itertools import islice
import json
from pathlib import Path
import time
import uuid
from urllib.error import URLError

from .combinations import candidate_pairs
from .fanduel import FanDuel, parse_quote, selections


def now():
    return datetime.now(timezone.utc).isoformat()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--event-id', help='FanDuel event ID, not MLB gamePk')
    parser.add_argument('--limit', type=int, default=10)
    parser.add_argument('--delay', type=float, default=1.0)
    parser.add_argument('--output', type=Path, default=Path('output'))
    args = parser.parse_args()
    if args.limit < 1 or args.delay < 1:
        parser.error('limit must be positive and delay must be at least 1 second')
    client = FanDuel()
    if not args.event_id:
        events = client.events().get('attachments', {}).get('events', {})
        for eid, event in events.items():
            print(eid, event.get('name'), event.get('openDate'))
        return 0
    run = args.output / (datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '-' + uuid.uuid4().hex[:8])
    run.mkdir(parents=True)
    raw = run / 'raw'
    raw.mkdir()
    market_time = now()
    data = client.markets(args.event_id)
    (raw / 'markets.json').write_text(json.dumps(data), encoding='utf-8')
    legs = selections(data, args.event_id)
    (run / 'selections.json').write_text(json.dumps(legs, indent=2), encoding='utf-8')
    fields = ['fd_event_id', 'game_id', 'event_name', 'state', 'combination_id', 'player_1', 'player_id_1',
              'prop_1', 'market_id_1', 'selection_id_1', 'individual_odds_1',
              'player_2', 'player_id_2', 'prop_2', 'market_id_2', 'selection_id_2',
              'individual_odds_2', 'market_timestamp', 'request_timestamp', 'response_timestamp',
              'status', 'failure_codes', 'odds', 'decimal_odds', 'probability', 'quote_id', 'raw_file']
    success = attempts = 0
    with (run / 'quotes.csv').open('w', newline='', encoding='utf-8') as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        for index, pair in enumerate(islice(candidate_pairs(legs), args.limit)):
            if index:
                time.sleep(args.delay)
            row = {'fd_event_id': args.event_id, 'game_id': '', 'state': client.state,
                   'event_name': data.get('attachments', {}).get('events', {}).get(
                       args.event_id, {}).get('name', ''),
                   'market_timestamp': market_time, 'request_timestamp': now(),
                   'combination_id': args.event_id + ':' + '|'.join(
                       s['market_id'] + ':' + s['selection_id'] for s in pair)}
            for n, leg in enumerate(pair, 1):
                for key in ('player', 'player_id', 'prop', 'market_id', 'selection_id', 'individual_odds'):
                    row[f'{key}_{n}'] = leg[key]
            try:
                response = client.quote(pair)
                row['response_timestamp'] = now()
                filename = f'raw/quote-{index:05d}.json'
                (run / filename).write_text(json.dumps(response), encoding='utf-8')
                row['raw_file'] = filename
                row.update(parse_quote(response, pair))
            except (RuntimeError, URLError, TimeoutError, OSError, ValueError) as error:
                row.update(status='request_error', failure_codes=type(error).__name__, response_timestamp=now())
                writer.writerow(row)
                stream.flush()
                print('Request failed; stopping batch. Check connectivity or endpoint access.')
                return 2
            writer.writerow(row)
            stream.flush()
            attempts += 1
            success += row['status'] == 'quoted'
    print(f'{success}/{attempts} verified SGP quotes; {len(legs)} eligible selections. Saved {run}')
    return 0 if success else 2


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (RuntimeError, URLError, ValueError, OSError) as error:
        print(f'Collector failed ({type(error).__name__}); check configuration and connectivity.')
        raise SystemExit(2)
