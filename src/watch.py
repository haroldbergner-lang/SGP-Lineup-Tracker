"""Persistent, local postseason collector. See README for timing and limitations."""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import csv
from datetime import datetime, timedelta, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import random
import subprocess
import sys
import threading
import time
from urllib.error import HTTPError
from zoneinfo import ZoneInfo

from .combinations import candidate_pairs
from .fanduel import FanDuel, parse_quote, quote_body, selections
from .lineups import fetch, get_json, normalize_name, official_order, parse_rotowire

ET = ZoneInfo('America/New_York')
UTC = timezone.utc


def stamp():
    return datetime.now(UTC).isoformat()


def dt(value):
    return datetime.fromisoformat(value.replace('Z', '+00:00'))


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(value, indent=2), encoding='utf-8')
    temporary.replace(path)


class RateLimit:
    """One start every quarter second, shared across all snapshot batches."""
    def __init__(self):
        self.lock = threading.Lock()
        self.next_start = 0.0
        self.stop_until = 0.0
        self.concurrent = threading.Semaphore(4)

    def wait(self):
        with self.lock:
            delay = max(self.next_start, self.stop_until) - time.monotonic()
            if delay > 0:
                time.sleep(delay)
            self.next_start = time.monotonic() + .25

    def backoff(self):
        with self.lock:
            self.stop_until = time.monotonic() + 300


def pair_id(event_id, pair):
    return event_id + ':' + '|'.join(sorted(s['market_id'] + ':' + s['selection_id'] for s in pair))


def resolve_event(game, events):
    """Require both exact team names, date/start proximity, and one unique match."""
    names = [normalize_name(game['teams'][s]['team']['name']) for s in ('away', 'home')]
    result = []
    for eid, event in events.items():
        parts = event.get('name', '').split(' @ ')
        if len(parts) != 2 or not event.get('openDate'):
            continue
        if all(name and name in normalize_name(part) for name, part in zip(names, parts)):
            if abs((dt(event['openDate']) - dt(game['gameDate'])).total_seconds()) <= 3 * 3600:
                result.append(str(eid))
    if len(result) != 1:
        raise ValueError('No unique FanDuel matchup for scheduled MLB game')
    return result[0]


QUOTE_FIELDS = ['combination_id', 'stage', 'requested_due', 'request_timestamp',
                'response_timestamp', 'status', 'failure_codes', 'odds', 'decimal_odds',
                'probability', 'quote_id', 'raw_file']


class Collector:
    def __init__(self, config):
        self.config = config
        self.root = Path(config['output']).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        os.environ['FD_STATE'] = config.get('state', 'nj')
        os.environ['FD_API_KEY'] = config['frontend_key']
        self.fd = FanDuel()
        self.rate = RateLimit()
        self.pool = ThreadPoolExecutor(max_workers=6)
        self.jobs = {}
        self.last_schedule = 0
        self.schedule = []
        self.last_lineups = {}
        self.health = {'started_at': stamp(), 'errors': []}
        self._lock_file = None

    def error(self, context, error):
        # Avoid URLs, frontend keys and quote tokens in service logs.
        entry = {'time': stamp(), 'context': context, 'error_type': type(error).__name__}
        if isinstance(error, RuntimeError):
            entry['detail'] = str(error)[:80] if str(error).startswith('FanDuel HTTP') else 'request failed'
        self.health['errors'] = (self.health['errors'] + [entry])[-40:]
        print(json.dumps(entry), flush=True)

    def refresh_schedule(self):
        data = get_json('https://statsapi.mlb.com/api/v1/schedule?sportId=1'
                        '&startDate=' + self.config['start_date'] + '&endDate=' + self.config['end_date']
                        + '&hydrate=team')
        self.schedule = [g for day in data.get('dates', []) for g in day.get('games', [])
                         if g.get('gameType') in ('L', 'W')
                         and self.config['start_date'] <= dt(g['gameDate']).astimezone(ET).date().isoformat()
                         <= self.config['end_date']]
        save(self.root / 'schedule.json', {'retrieved_at': stamp(), 'games': self.schedule})
        self.last_schedule = time.monotonic()

    def orders_for(self, date):
        cache = self.last_lineups.get(date)
        if cache and time.monotonic() - cache[0] < 55:
            return cache[1]
        url = 'https://www.rotowire.com/baseball/daily-lineups.php?date=' + date
        html = fetch(url)
        orders = parse_rotowire(html, date)
        moment = stamp()
        # Preserve source evidence for every poll; never overwrite projections.
        filename = moment.replace(':', '-') + '.html'
        path = self.root / 'lineup_sources' / date / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(html, encoding='utf-8')
        result = (orders, moment, str(path.relative_to(self.root)))
        self.last_lineups[date] = (time.monotonic(), result)
        return result

    def quote_one(self, manifest, stage, due, pair, directory, cutoff):
        cid = pair_id(manifest['fd_event_id'], pair)
        row = {'combination_id': cid, 'stage': stage, 'requested_due': due,
               'request_timestamp': stamp()}
        if datetime.now(UTC) >= cutoff:
            return {**row, 'response_timestamp': stamp(), 'status': 'skipped_at_start'}
        with self.rate.concurrent:
            self.rate.wait()
            row['request_timestamp'] = stamp()
            if datetime.now(UTC) >= cutoff:
                return {**row, 'response_timestamp': stamp(), 'status': 'skipped_at_start'}
            try:
                response = self.fd.quote(pair)
                row['response_timestamp'] = stamp()
                filename = hashlib.sha256(cid.encode()).hexdigest()[:20] + '.json'
                raw = directory / 'raw' / filename
                save(raw, {'requested_at': row['request_timestamp'],
                           'received_at': row['response_timestamp'],
                           'request': quote_body(pair), 'response': response})
                row['raw_file'] = str(raw.relative_to(self.root))
                row.update(parse_quote(response, pair))
                if dt(row['response_timestamp']) >= cutoff:
                    row['status'] = 'response_at_or_after_start'
            except Exception as error:
                row.update(status='request_error', failure_codes=type(error).__name__, response_timestamp=stamp())
                if isinstance(error, RuntimeError) and any(code in str(error) for code in ('429', '403', '503')):
                    self.rate.backoff()
                self.error(stage, error)
        return row

    def snapshot(self, manifest, stage, due):
        directory = self.root / str(manifest['game_id']) / 'snapshots' / stage
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / 'quotes.csv'
        completed = set()
        if path.exists():
            with path.open(newline='') as stream:
                completed = {r['combination_id'] for r in csv.DictReader(stream)}
        pairs = [pair for pair in manifest['pairs']
                 if pair_id(manifest['fd_event_id'], pair) not in completed]
        cutoff = dt(manifest['scheduled_start'])
        with path.open('a', newline='', encoding='utf-8') as stream:
            writer = csv.DictWriter(stream, fieldnames=QUOTE_FIELDS)
            if stream.tell() == 0:
                writer.writeheader()
            with ThreadPoolExecutor(max_workers=4) as pool:
                futures = [pool.submit(self.quote_one, manifest, stage, due, pair, directory, cutoff)
                           for pair in pairs]
                for future in as_completed(futures):
                    writer.writerow(future.result())
                    stream.flush()
        save(directory / 'done.json', {'completed_at': stamp(), 'stage': stage})
        return stage

    def launch(self, manifest, state, stage, due):
        key = (manifest['game_id'], stage)
        done = self.root / str(manifest['game_id']) / 'snapshots' / stage / 'done.json'
        if key not in self.jobs and not done.exists():
            state.setdefault('stages', {})[stage] = {'due': due, 'started_at': stamp()}
            self.jobs[key] = self.pool.submit(self.snapshot, manifest.copy(), stage, due)

    def initialize(self, game, orders, retrieved, source):
        directory = self.root / str(game['gamePk'])
        state_path = directory / 'state.json'
        if state_path.exists():
            state = json.loads(state_path.read_text())
        else:
            box = get_json(f'https://statsapi.mlb.com/api/v1/game/{game["gamePk"]}/boxscore')
            state = {'game_id': game['gamePk'], 'baseline_observed_at': retrieved,
                     'projected': {}, 'confirmed': {}, 'last_unconfirmed': {},
                     'baseline_already_confirmed': [], 'stages': {}}
            for side in ('away', 'home'):
                order = orders.get(side)
                if order and order['status'] == 'expected' and not official_order(box, side):
                    state['projected'][side] = {**order, 'retrieved_at': retrieved, 'raw_file': source}
                    state['last_unconfirmed'][side] = retrieved
                elif official_order(box, side) or (order and order['status'] == 'confirmed'):
                    state['baseline_already_confirmed'].append(side)
            # Freeze projections even if FanDuel discovery is temporarily unavailable.
            save(state_path, state)
        if (directory / 'manifest.json').exists():
            return state
        events = self.fd.events().get('attachments', {}).get('events', {})
        eid = resolve_event(game, events)
        markets = self.fd.markets(eid)
        legs = selections(markets, eid)
        if not legs:
            raise ValueError('No eligible MLB selections available at baseline')
        pairs = list(candidate_pairs(legs))
        random.Random(game['gamePk']).shuffle(pairs)
        manifest = {'game_id': game['gamePk'], 'fd_event_id': eid,
                    'scheduled_start': game['gameDate'], 'created_at': stamp(),
                    'teams': {s: game['teams'][s]['team'] for s in ('away', 'home')},
                    'selections': legs, 'pairs': pairs, 'state': self.fd.state,
                    'market_retrieved_at': stamp()}
        save(directory / 'markets.json', markets)
        save(directory / 'manifest.json', manifest)
        save(state_path, state)
        self.launch(manifest, state, 'baseline', (dt(game['gameDate']) - timedelta(hours=5)).isoformat())
        print(f'Baseline manifest saved: {game["gamePk"]}, {len(pairs)} candidate pairs', flush=True)
        return state

    def process_game(self, game, current):
        start = dt(game['gameDate'])
        directory = self.root / str(game['gamePk'])
        if current < start - timedelta(hours=5) or current >= start:
            return
        if any(word in game['status']['detailedState'].lower() for word in ('cancel', 'postpon')):
            return
        date = start.astimezone(ET).date().isoformat()
        try:
            all_orders, retrieved, source = self.orders_for(date)
        except Exception as error:
            self.error('projection_source', error)
            all_orders, retrieved, source = {}, stamp(), ''
        teams = tuple(game['teams'][side]['team'].get('abbreviation', '') for side in ('away', 'home'))
        orders = all_orders.get(teams, {})
        state = self.initialize(game, orders, retrieved, source)
        manifest = json.loads((directory / 'manifest.json').read_text())
        # Record schedule changes without replacing baseline projections or pairs.
        if manifest['scheduled_start'] != game['gameDate']:
            state.setdefault('schedule_changes', []).append({'detected_at': stamp(), 'start': game['gameDate']})
            manifest['scheduled_start'] = game['gameDate']
            save(directory / 'manifest.json', manifest)
        baseline_due = (start - timedelta(hours=5)).isoformat()
        self.launch(manifest, state, 'baseline', baseline_due)
        try:
            box = get_json(f'https://statsapi.mlb.com/api/v1/game/{game["gamePk"]}/boxscore')
            box_source = directory / 'boxscores' / (stamp().replace(':', '-') + '.json')
            save(box_source, box)
        except Exception as error:
            self.error('official_lineup', error)
            box, box_source = {}, None
        for side in ('away', 'home'):
            actual = official_order(box, side)
            order = orders.get(side)
            if not actual:
                state['last_unconfirmed'][side] = stamp()
            if actual:
                corroborated = bool(order and order['status'] == 'confirmed' and
                                    [normalize_name(p['name']) for p in actual] ==
                                    [normalize_name(p['name']) for p in order['players']])
                if side not in state['confirmed']:
                    state['confirmed'][side] = {'players': actual, 'source': 'MLB Stats API published battingOrder',
                                                'detected_at': stamp(), 'source_published_at': None,
                                                'last_unconfirmed_at': state['last_unconfirmed'].get(side),
                                                'raw_file': str(box_source.relative_to(self.root)),
                                                'rotowire_corroborated': corroborated}
                    print(f'Confirmed order detected: {game["gamePk"]} {side}', flush=True)
                else:
                    confirmed = state['confirmed'][side]
                    confirmed['rotowire_corroborated'] = corroborated
                    if confirmed['players'] != actual:
                        confirmed.setdefault('revisions', []).append({'detected_at': stamp(), 'previous_players': confirmed['players']})
                        confirmed['players'] = actual
                        confirmed['raw_file'] = str(box_source.relative_to(self.root))
            confirmed = state['confirmed'].get(side)
            if confirmed:
                detected = dt(confirmed['detected_at'])
                self.launch(manifest, state, side + '_immediate', detected.isoformat())
                due = detected + timedelta(minutes=10)
                if current >= due:
                    self.launch(manifest, state, side + '_plus10', due.isoformat())
        # Start the final sweep early enough for the full universe at four requests/sec.
        lead = max(300, len(manifest['pairs']) * .25 + 90)
        due = start - timedelta(seconds=lead)
        if current >= due:
            self.launch(manifest, state, 'pregame', due.isoformat())
        save(directory / 'state.json', state)

    def tick(self):
        if time.monotonic() - self.last_schedule > 900 or not self.schedule:
            self.refresh_schedule()
        current = datetime.now(UTC)
        for game in self.schedule:
            try:
                self.process_game(game, current)
            except Exception as error:
                self.error('game_' + str(game['gamePk']), error)
        for key, future in list(self.jobs.items()):
            if future.done():
                try:
                    future.result()
                except Exception as error:
                    self.error('snapshot_' + key[1], error)
                del self.jobs[key]
        self.health.update(heartbeat_at=stamp(), running_batches=len(self.jobs),
                           scheduled_games=len(self.schedule), next_games=[
                               {'game_id': g['gamePk'], 'start': g['gameDate']} for g in self.schedule
                               if dt(g['gameDate']) > current])
        save(self.root / 'health.json', self.health)
        from .analysis import export_all
        export_all(self.root)

    def run(self, once=False):
        self._lock_file = (self.root / 'collector.lock').open('a')
        try:
            fcntl.flock(self._lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print('Collector already running', flush=True)
            return
        save(self.root / 'process.json', {'pid': os.getpid(), 'started_at': stamp()})
        deadline = datetime.fromisoformat(self.config['end_date']).replace(tzinfo=ET) + timedelta(days=1, hours=3)
        try:
            while datetime.now(UTC) < deadline:
                try:
                    self.tick()
                except Exception as error:
                    self.error('tick', error)
                    self.health['heartbeat_at'] = stamp()
                    save(self.root / 'health.json', self.health)
                if once:
                    break
                time.sleep(30)
        finally:
            self.pool.shutdown(wait=True)
            from .analysis import export_all
            export_all(self.root)
            self._lock_file.close()


def ensure(config_path, config):
    root = Path(config['output']).resolve()
    root.mkdir(parents=True, exist_ok=True)
    # Check the OS lock, not just a possibly stale process ID.
    with (root / 'collector.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print('Collector is running')
            return
    deadline = datetime.fromisoformat(config['end_date']).replace(tzinfo=ET) + timedelta(days=1, hours=3)
    if datetime.now(UTC) >= deadline:
        print('Collection window ended')
        return
    with (root / 'collector.log').open('a') as log:
        process = subprocess.Popen([sys.executable, '-m', 'src.watch', '--config', str(config_path.resolve())],
                                   cwd=Path(__file__).resolve().parent.parent, stdout=log, stderr=log,
                                   start_new_session=True)
    print('Collector launched')
    return process.pid


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--once', action='store_true')
    parser.add_argument('--ensure', action='store_true')
    args = parser.parse_args()
    config = json.loads(args.config.read_text())
    if args.ensure:
        ensure(args.config, config)
    else:
        Collector(config).run(once=args.once)


if __name__ == '__main__':
    main()
