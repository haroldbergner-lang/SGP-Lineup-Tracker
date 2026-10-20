import csv
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from src.analysis import export_all
from src.latency import ranked_pairs, REPEAT_PAIR_LIMIT
from src.watch import Collector, RateLimit, save, stamp
from src.combinations import candidate_pairs
from test_latency import leg
from test_workflow import FakeFD, boxscore


class QueueAndRolling(unittest.TestCase):
    def test_repeat_overtakes_queued_full_sweep_and_rolling_requests(self):
        rate = RateLimit()
        rate.interval = 0
        rate.active = 4  # simulate four in-flight network requests
        starts = []
        def quote(name, priority):
            with rate.slot(priority) as admitted:
                self.assertTrue(admitted)
                starts.append(name)
        with ThreadPoolExecutor(max_workers=3) as pool:
            futures = []
            for n, (name, priority) in enumerate([('rolling',2),('full',1),('repeat',0)],1):
                futures.append(pool.submit(quote,name,priority))
                with rate.condition:
                    self.assertTrue(rate.condition.wait_for(lambda:len(rate.waiters)==n,timeout=2))
            with rate.condition:
                rate.active = 0
                rate.condition.notify_all()
            for future in futures:
                future.result(timeout=2)
        self.assertEqual(starts, ['repeat','full','rolling'])
        self.assertEqual(rate.active,0)

    def test_request_spacing_concurrency_and_backoff_deadline(self):
        rate = RateLimit()
        rate.interval = .025
        starts, active_counts = [], []
        def quote():
            with rate.slot() as admitted:
                self.assertTrue(admitted)
                starts.append(time.monotonic())
                active_counts.append(rate.active)
                time.sleep(.12)
        with ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(lambda _:quote(),range(8)))
        starts.sort()
        self.assertTrue(all(b-a >= .024 for a,b in zip(starts,starts[1:])))
        self.assertLessEqual(max(active_counts),4)
        rate.backoff()
        with rate.slot(0,datetime.now(timezone.utc)+timedelta(seconds=.02)) as admitted:
            self.assertFalse(admitted)
        self.assertEqual(rate.waiters,[])

    def test_realistic_lineup_sample_is_diverse_and_includes_changed_relationship(self):
        names=['Diaz','Aranda','Hicks','Caminero','Simpson','Mesa','DeLuca','Palacios','Walls']
        legs=[leg(name,n*2+(prop=='1+ RBI'),prop) for n,name in enumerate(names)
              for prop in ('1+ run','1+ RBI')]
        pairs=list(candidate_pairs(legs))
        projected=['Diaz','Aranda','Caminero','Vilade','DeLuca','Simpson','Fortes','Mateo','Walls']
        state={'projected':{'home':{'players':[{'name':n,'position':i+1} for i,n in enumerate(projected)]}},
               'confirmed':{'home':{'players':[{'name':n,'position':i+1} for i,n in enumerate(names)]}}}
        ordered, sample=ranked_pairs({'pairs':pairs},state,'home')
        selected=[tuple(s['player'] for s in pair) for pair in sample]
        self.assertIn(('Hicks','Simpson'), selected)
        self.assertLessEqual(len(sample),REPEAT_PAIR_LIMIT)
        self.assertGreaterEqual(len({p[0] for p in selected}),5)
        self.assertTrue(all(p[0] in names and p[1] in names for p in selected))
        self.assertEqual(len(ordered),len(pairs))

    def test_rolling_snapshot_is_resumed_and_exports_actual_detection_boundary(self):
        with tempfile.TemporaryDirectory() as d:
            c=Collector({'output':d,'frontend_key':'test'})
            c.rate.interval=0
            now=datetime.now(timezone.utc)
            start=(now+timedelta(hours=2)).isoformat()
            c.fd=FakeFD(start)
            pair=[leg('A',1),leg('B',2,'1+ RBI')]
            manifest={'game_id':100,'fd_event_id':'500','pairs':[pair], 'state':'nj',
                      'scheduled_start':start,'teams':{'away':{'name':'Rays'},'home':{'name':'Guardians'}}}
            game={'gamePk':100,'gameDate':start,'status':{'detailedState':'Scheduled'},
                  'teams':{s:{'team':{'abbreviation':a}} for s,a in [('away','TB'),('home','CLE')]}}
            state={'game_id':100,'baseline_observed_at':(now-timedelta(minutes=6)).isoformat(),
                   'projected':{},'confirmed':{},'last_unconfirmed':{}}
            save(Path(d)/'100/manifest.json',manifest);save(Path(d)/'100/state.json',state)
            with patch.object(c,'orders_for',return_value=({},stamp(),'')), \
                 patch('src.watch.get_json',return_value=boxscore(False)):
                c.process_game(game,now)
                for future in list(c.jobs.values()):future.result(timeout=2)
                count=len(c.fd.quoted)
                c.process_game(game,now)
                self.assertEqual(len(c.fd.quoted),count)
            rolling=list((Path(d)/'100/snapshots').glob('rolling_*/quotes.csv'))
            self.assertEqual(len(rolling),1)
            export_all(d)
            with (Path(d)/'rolling_quotes.csv').open() as stream:
                rows=list(csv.DictReader(stream))
            self.assertEqual(len(rows),1)
            self.assertEqual(rows[0]['status'],'quoted')
            # Reclassify against a detection before the quote: never call it pre-lineup.
            state=json.loads((Path(d)/'100/state.json').read_text())
            state['confirmed']['away']={'detected_at':(now-timedelta(seconds=1)).isoformat()}
            save(Path(d)/'100/state.json',state);export_all(d)
            with (Path(d)/'rolling_quotes.csv').open() as stream:
                self.assertEqual(next(csv.DictReader(stream))['before_away_detection'],'False')
            c.pool.shutdown(wait=True)
