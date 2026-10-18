import csv
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from src.analysis import export_all
from src.fanduel import selections
from src.combinations import candidate_pairs
from src.latency import ranked_pairs, REPEAT_PAIR_LIMIT
from src.watch import Collector, pair_id, save, stamp
from test_workflow import FakeFD, boxscore, order_html
from src.lineups import parse_rotowire


def leg(name, number, prop='1+ run'):
    return {'player': name, 'market_id': '1', 'selection_id': str(number),
            'prop': prop, 'team': 'tampa_bay_rays'}


class Latency(unittest.TestCase):
    def test_changed_players_rank_first_and_sampling_is_bounded(self):
        stable = [leg('Stable', 1), leg('Other', 2, '1+ RBI')]
        changed = [[leg('New Starter Jr.', n), leg('Stable', 1, '1+ RBI')]
                   for n in range(3, 15)]
        manifest = {'pairs': [stable] + changed}
        state = {'projected': {'away': {'players': [{'name': 'Stable', 'position': 1},
                    {'name': 'Other', 'position': 2}]}},
                 'confirmed': {'away': {'players': [{'name': 'Stable', 'position': 1},
                    {'name': 'Other', 'position': 2}, {'name': 'New Starter', 'position': 3}]}}}
        ordered, priority = ranked_pairs(manifest, state, 'away')
        self.assertEqual(ordered[-1], stable)
        self.assertEqual(len(priority), REPEAT_PAIR_LIMIT)
        self.assertEqual(len(ordered), len(manifest['pairs']))
        self.assertEqual(manifest['pairs'][0], stable)  # baseline never mutated
        self.assertEqual(ranked_pairs(manifest, {}, 'away'), (manifest['pairs'], []))

    def test_expired_repeat_does_not_send_requests_or_replace_immediate(self):
        with tempfile.TemporaryDirectory() as d:
            c = Collector({'output': d, 'frontend_key': 'test'})
            c.fd = FakeFD(stamp())
            c.rate.wait = lambda: None
            pair = [leg('A', 1), leg('B', 2, '1+ RBI')]
            manifest = {'game_id': 1, 'fd_event_id': '500', 'pairs': [pair],
                        'scheduled_start': (datetime.now(timezone.utc)+timedelta(hours=1)).isoformat()}
            due = (datetime.now(timezone.utc)-timedelta(seconds=30)).isoformat()
            c.snapshot(manifest, 'away_latency_10s', due)
            c.snapshot(manifest, 'away_latency_10s', due)
            path = Path(d)/'1/snapshots/away_latency_10s/quotes.csv'
            with path.open() as stream:
                rows = list(csv.DictReader(stream))
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]['status'], 'sampling_window_missed')
            self.assertEqual(c.fd.quoted, [])
            c.snapshot(manifest, 'away_immediate', stamp())
            self.assertEqual(len(c.fd.quoted), 1)
            c.pool.shutdown(wait=True)

    def test_repeat_scheduler_skips_stale_offsets_and_keeps_fast_polling(self):
        with tempfile.TemporaryDirectory() as d:
            c = Collector({'output': d, 'frontend_key': 'test'})
            now = datetime.now(timezone.utc)
            start = (now + timedelta(hours=2)).isoformat()
            c.fd = FakeFD(start)
            game = {'gamePk': 100, 'gameDate': start, 'status': {'detailedState': 'Scheduled'},
                    'teams': {s: {'team': {'name': n, 'abbreviation': a}} for s,n,a in
                    [('away','Tampa Bay Rays','TB'),('home','Cleveland Guardians','CLE')]}}
            pair_stable = [leg('visit Player 8', 8),leg('visit Player 7', 7, '1+ RBI')]
            pair_changed = [leg('visit Player 0', 1),leg('visit Player 1', 2, '1+ RBI')]
            m = {'game_id': 100, 'fd_event_id': '500', 'pairs': [pair_stable,pair_changed],
                 'scheduled_start': start}
            orders = parse_rotowire(order_html(), '2026-10-17')[('TB','CLE')]
            actual = boxscore(True)
            from src.lineups import official_order
            changed_projection = json.loads(json.dumps(orders))
            changed_projection['away']['players'][0]['position'] = 4
            state = {'projected': changed_projection, 'last_unconfirmed': {},
                     'confirmed': {s:{'players':official_order(actual,s),
                        'detected_at':(now-timedelta(seconds=21)).isoformat()}
                        for s in ('away','home')}}
            save(Path(d)/'100/manifest.json',m);save(Path(d)/'100/state.json',state)
            with patch.object(c,'orders_for',return_value=({},stamp(),'')), \
                 patch('src.watch.get_json',return_value=actual), \
                 patch.object(c,'launch') as launch, patch.object(c,'launch_refresh'):
                c.process_game(game,now)
            calls = {call.args[2]:call for call in launch.call_args_list}
            self.assertIn('away_latency_20s', calls)
            self.assertNotIn('away_latency_10s', calls)
            self.assertEqual(calls['away_immediate'].args[4][0],pair_changed)
            self.assertNotIn('home_latency_20s', calls)  # unchanged order
            c.schedule=[game]
            self.assertEqual(c.poll_interval(now),5)
            self.assertEqual(c.poll_interval(now+timedelta(seconds=80)),30)
            c.pool.shutdown(wait=True)

    def test_added_markets_export_separately_and_resume_without_duplicates(self):
        with tempfile.TemporaryDirectory() as d:
            c = Collector({'output':d,'frontend_key':'test'})
            c.rate.wait=lambda:None
            start=(datetime.now(timezone.utc)+timedelta(hours=1)).isoformat()
            c.fd=FakeFD(start)
            baseline=list(candidate_pairs(selections(c.fd.markets('500'),'500')))[0]
            manifest={'game_id':100,'fd_event_id':'500','pairs':[baseline], 'state':'nj',
                      'scheduled_start':start,'teams':{'away':{'name':'Rays'},'home':{'name':'Guardians'}}}
            save(Path(d)/'100/manifest.json',manifest)
            save(Path(d)/'100/state.json',{'confirmed':{'away':{'detected_at':stamp(),
                'latency_sampling':{'selected_combination_ids':[pair_id('500',baseline)]}}},'projected':{}})
            c.snapshot(manifest,'baseline',stamp())
            c.refresh_markets(manifest,'away',stamp())
            extra=json.loads((Path(d)/'100/market_refresh/away/manifest.json').read_text())
            self.assertEqual(len(extra['pairs']),1)  # another eligible run player
            self.assertNotEqual(pair_id('500',extra['pairs'][0]),pair_id('500',baseline))
            self.assertFalse(extra['baseline_available'])
            c.snapshot(extra,'away_new_markets',stamp())
            c.snapshot(extra,'away_new_markets',stamp())
            c.snapshot(manifest,'away_latency_10s',stamp())
            self.assertEqual(export_all(d),1)
            def read(name):
                with (Path(d)/name).open() as stream:
                    return list(csv.DictReader(stream))
            main=read('sgp_price_changes.csv')
            added=read('additional_sgp_quotes.csv')
            repeat=read('latency_quotes.csv')
            self.assertEqual(len(main),1);self.assertEqual(main[0]['odds_before'],'300.0')
            self.assertEqual(len(added),1);self.assertEqual(added[0]['cohort'],'post_announcement_only')
            self.assertEqual(added[0]['status'],'quoted')
            self.assertEqual(len(repeat),6)
            self.assertEqual(sum(r['status']=='quoted' for r in repeat),1)
            self.assertEqual(len(c.fd.quoted),3)
            c.pool.shutdown(wait=True)
