import csv
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from src.analysis import export_all, player_fields
from src.lineups import normalize_name, official_order, parse_rotowire
from src.watch import Collector, pair_id, resolve_event, save, stamp


def order_html(date='2026-10-17', status='expected', count=9):
    lists = []
    for side in ('visit', 'home'):
        players = ''.join(f'<li class="lineup__player"><a title="{side} Player {i}" href="/baseball/player/player-{100+i}">P</a></li>' for i in range(count))
        lists.append(f'<ul class="lineup__list is-{side}"><li class="lineup__status is-{status}">Status</li>{players}</ul>')
    return f'<div class="lineup is-mlb"><div class="lineup__abbr">TB</div><div class="lineup__abbr">CLE</div><a class="lineup__matchup" href="/baseball/box-score/game-{date}-123"></a>{"".join(lists)}</div>'


def boxscore(confirmed=False):
    result = {'teams': {}}
    for side in ('away', 'home'):
        names = ['visit' if side == 'away' else 'home']
        result['teams'][side] = {'battingOrder': list(range(1, 10)) if confirmed else [],
                                 'players': {f'ID{i}': {'person': {'fullName': names[0] + f' Player {i-1}'}}
                                             for i in range(1, 10)}}
    return result


class Sources(unittest.TestCase):
    def test_expected_and_confirmed(self):
        for status in ('expected', 'confirmed'):
            result = parse_rotowire(order_html(status=status), '2026-10-17')
            self.assertEqual(result[('TB', 'CLE')]['away']['status'], status)
            self.assertEqual(len(result[('TB', 'CLE')]['home']['players']), 9)

    def test_wrong_date_or_partial_rejected(self):
        self.assertEqual(parse_rotowire(order_html(), '2026-10-18'), {})
        self.assertEqual(parse_rotowire(order_html(count=8), '2026-10-17'), {})

    def test_duplicate_matchup_rejected(self):
        with self.assertRaises(ValueError):
            parse_rotowire(order_html() * 2, '2026-10-17')

    def test_name_suffix_and_accent(self):
        self.assertEqual(normalize_name('Victor Mesa Jr.'), normalize_name('Victor Mesa'))
        self.assertEqual(normalize_name('Yandy Díaz'), normalize_name('Yandy Diaz'))
        self.assertNotEqual(normalize_name('Y. Diaz'), normalize_name('Yandy Diaz'))

    def test_official_requires_nine(self):
        self.assertIsNone(official_order(boxscore(False), 'away'))
        self.assertEqual(len(official_order(boxscore(True), 'away')), 9)

    def test_unknown_starter_is_not_false(self):
        fields = player_fields({'player': 'Player'}, {})
        self.assertEqual(fields['projected_starter'], '')
        self.assertEqual(fields['confirmed_starter'], '')


class FakeFD:
    state = 'nj'

    def __init__(self, start):
        self.start = start
        self.quoted = []

    def events(self):
        return {'attachments': {'events': {'500': {'name': 'Tampa Bay Rays @ Cleveland Guardians', 'openDate': self.start}}}}

    def markets(self, eid):
        markets = {}
        for i in range(3):
            markets[f'1.{i}'] = {
                'eventId': 500, 'sgmMarket': True, 'marketStatus': 'OPEN',
                'marketType': 'TO_RECORD_AN_RBI' if i == 0 else 'TO_RECORD_A_RUN',
                'runners': [{'selectionId': i+1, 'runnerName': f'visit Player {i}',
                             'secondaryLogo': 'https://assets.sportsbook.fanduel.com/images/team/mlb/tampa_bay_rays.png',
                             'runnerStatus': 'ACTIVE', 'isPlayerSelection': True}]}
        return {'attachments': {'markets': markets}}

    def quote(self, pair):
        self.quoted.append(tuple((s['market_id'], s['selection_id']) for s in pair))
        return {'respCode': 'SUCCESS', 'betCombinations': [{
            'isSGM': True, 'betType': 'DOUBLE', 'legCombinations': [
                {'runners': [{'marketId': s['market_id'], 'selectionId': int(s['selection_id'])}]} for s in pair],
            'winAvgOdds': {'trueOdds': {'decimalOdds': {'decimalOdds': 4}},
                           'americanDisplayOdds': {'americanOdds': 300}}}]}


class Workflow(unittest.TestCase):
    def test_event_mapping_rejects_duplicates_and_wrong_game(self):
        start = datetime.now(timezone.utc).isoformat()
        game = {'gameDate': start, 'teams': {s: {'team': {'name': name}} for s, name in
                                           [('away', 'Tampa Bay Rays'), ('home', 'Cleveland Guardians')]}}
        events = FakeFD(start).events()['attachments']['events']
        self.assertEqual(resolve_event(game, events), '500')
        with self.assertRaises(ValueError):
            resolve_event(game, {**events, '501': events['500']})
        with self.assertRaises(ValueError):
            resolve_event(game, {'500': {'name': 'Other Team @ Cleveland Guardians', 'openDate': start}})

    def test_baseline_confirmations_repeat_prices_export_and_resume(self):
        with tempfile.TemporaryDirectory() as directory:
            current = datetime.now(timezone.utc)
            start = (current + timedelta(hours=4)).isoformat()
            config = {'output': directory, 'frontend_key': 'test-public-key',
                      'start_date': '2026-10-17', 'end_date': '2026-11-01'}
            collector = Collector(config)
            collector.fd = FakeFD(start)
            collector.rate.wait = lambda: None
            game = {'gamePk': 100, 'gameDate': start, 'status': {'detailedState': 'Scheduled'},
                    'teams': {s: {'team': {'name': name, 'abbreviation': abbr}} for s, name, abbr in
                              [('away', 'Tampa Bay Rays', 'TB'), ('home', 'Cleveland Guardians', 'CLE')]}}
            orders = parse_rotowire(order_html(), '2026-10-17')
            with patch.object(collector, 'orders_for', return_value=(orders, stamp(), 'source.html')):
                with patch('src.watch.get_json', return_value=boxscore(False)):
                    collector.process_game(game, current)
                for future in collector.jobs.values():
                    future.result(timeout=5)
                collector.jobs.clear()
                manifest = json.loads((Path(directory)/'100/manifest.json').read_text())
                self.assertEqual(len(manifest['pairs']), 2)
                with patch('src.watch.get_json', return_value=boxscore(True)):
                    collector.process_game(game, current)
                for future in collector.jobs.values():
                    future.result(timeout=5)
                collector.jobs.clear()
                state_path = Path(directory)/'100/state.json'
                state = json.loads(state_path.read_text())
                self.assertEqual(len(state['confirmed']), 2)
                for side in ('away', 'home'):
                    state['confirmed'][side]['detected_at'] = (current - timedelta(minutes=11)).isoformat()
                save(state_path, state)
                with patch('src.watch.get_json', return_value=boxscore(True)):
                    collector.process_game(game, current)
                for future in collector.jobs.values():
                    future.result(timeout=5)
                count = len(collector.fd.quoted)
                self.assertEqual(count, 10)  # two pairs across baseline and four follow-ups
                # A resumed finished snapshot never duplicates the existing rows or quotes.
                collector.snapshot(manifest, 'baseline', stamp())
                self.assertEqual(len(collector.fd.quoted), count)
                self.assertEqual(export_all(directory), 2)
                with (Path(directory)/'sgp_price_changes.csv').open() as stream:
                    rows = list(csv.DictReader(stream))
                self.assertTrue(all(r['odds_before'] == '300.0' and r['odds_after'] == '300.0' for r in rows))
                self.assertTrue(all(r['probability_change'] == '0.0' for r in rows))
                self.assertEqual(rows[0]['primary_after_stage'], 'away_plus10')
                self.assertEqual(len(json.loads(rows[0]['away_projected_lineup'])), 9)
                self.assertEqual(len(json.loads(rows[0]['home_actual_lineup'])), 9)
            collector.pool.shutdown(wait=True)

    def test_projections_survive_market_discovery_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            current = datetime.now(timezone.utc)
            start = (current + timedelta(hours=4)).isoformat()
            collector = Collector({'output': directory, 'frontend_key': 'test-public-key'})
            collector.fd = FakeFD(start)
            game = {'gamePk': 101, 'gameDate': start, 'teams': {s: {'team': {'name': name}} for s, name in
                        [('away', 'Tampa Bay Rays'), ('home', 'Cleveland Guardians')]}}
            orders = parse_rotowire(order_html(), '2026-10-17')[('TB', 'CLE')]
            with patch('src.watch.get_json', return_value=boxscore(False)):
                with patch.object(collector.fd, 'events', side_effect=RuntimeError('temporary failure')):
                    with self.assertRaises(RuntimeError):
                        collector.initialize(game, orders, 'original-baseline-time', 'original-source.html')
            state = json.loads((Path(directory)/'101/state.json').read_text())
            self.assertEqual(state['baseline_observed_at'], 'original-baseline-time')
            self.assertEqual(len(state['projected']['away']['players']), 9)
            self.assertFalse((Path(directory)/'101/manifest.json').exists())
            collector.pool.shutdown(wait=True)

    def test_no_game_collection_before_five_hours_or_after_start(self):
        with tempfile.TemporaryDirectory() as directory:
            collector = Collector({'output': directory, 'frontend_key': 'test-public-key'})
            current = datetime.now(timezone.utc)
            for offset in (6, -1):
                game = {'gamePk': 100, 'gameDate': (current + timedelta(hours=offset)).isoformat()}
                with patch.object(collector, 'orders_for') as source:
                    collector.process_game(game, current)
                    source.assert_not_called()
            collector.pool.shutdown(wait=True)


if __name__ == '__main__':
    unittest.main()
