"""GitHub-hosted, bounded collection with encrypted checkpoint recovery."""
import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import signal
import time

from .analysis import export_all
from .cloud_storage import download, publish, gh
from .watch import Collector, ET, dt, save, stamp


def eligible_games(games, current):
    result = []
    for game in games:
        start = dt(game['gameDate'])
        baseline = start - timedelta(hours=5)
        state = game['status']['detailedState'].lower()
        if any(word in state for word in ('cancel', 'postpon')):
            continue
        if baseline <= current + timedelta(minutes=55) and start > current:
            result.append(game)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--restore-only', action='store_true')
    args = parser.parse_args()
    if datetime.now(ET).date().isoformat() > '2026-11-02':
        gh(['workflow', 'disable', 'collect.yml'])
        print('Collection window ended; cloud schedule disabled.')
        return 0
    def keep_archive():
        if os.environ.get('GITHUB_OUTPUT'):
            with open(os.environ['GITHUB_OUTPUT'], 'a') as stream:
                stream.write('keep_archive=true\n')
    if args.restore_only:
        root = download(Path('data'))
        (root/'process.json').unlink(missing_ok=True)
        keep_archive()
        publish(root)
        print('Encrypted cloud archive restored, authenticated, and saved back to GitHub.')
        return 0
    root = Path('data/season-2026')
    config = {'state': 'nj', 'frontend_key': os.environ['FD_API_KEY'],
              'start_date': '2026-10-17', 'end_date': '2026-11-01', 'output': str(root)}
    collector = Collector(config)
    collector.refresh_schedule()
    current = datetime.now(timezone.utc)
    games = eligible_games(collector.schedule, current)
    if not games:
        print('No game requires collection in this window.')
        return 0
    # Avoid downloading or archiving growing datasets on idle hourly checks.
    root = download(Path('data'))
    (root/'process.json').unlink(missing_ok=True)
    keep_archive()
    ids = {g['gamePk'] for g in games}
    original_refresh = collector.refresh_schedule
    def refresh():
        original_refresh()
        collector.schedule = [g for g in collector.schedule if g['gamePk'] in ids]
    refresh()
    deadline = min(max(dt(g['gameDate']) for g in games) + timedelta(minutes=1),
                   current + timedelta(minutes=354))
    stopping = False
    def stop(signum, frame):
        nonlocal stopping
        stopping = True
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    last_upload = 0.0
    uploads = ThreadPoolExecutor(max_workers=1)
    checkpoint = None
    try:
        while datetime.now(timezone.utc) < deadline and not stopping:
            tick_started = time.monotonic()
            collector.tick()
            if checkpoint is not None and checkpoint.done():
                try:
                    checkpoint.result()
                except Exception as error:
                    collector.error('cloud_checkpoint', error)
                checkpoint = None
            if checkpoint is None and time.monotonic() - last_upload > 300:
                # Archive uploads must not block lineup polling or repeat scheduling.
                checkpoint = uploads.submit(publish, root)
                last_upload = time.monotonic()
            # Once the refreshed scheduled start passes, finish all pending writes.
            if collector.schedule and all(datetime.now(timezone.utc) >= dt(g['gameDate'])
                                          for g in collector.schedule) and not collector.jobs and not collector.refresh_jobs:
                break
            time.sleep(max(.1, collector.poll_interval() - (time.monotonic() - tick_started)))
    finally:
        uploads.shutdown(wait=True)
        if checkpoint is not None:
            try:
                checkpoint.result()
            except Exception as error:
                collector.error('cloud_checkpoint', error)
        collector.pool.shutdown(wait=True)
        export_all(root)
        save(root/'cloud-run.json', {'finished_at': stamp(), 'game_ids': sorted(ids),
                                    'run_id': os.environ.get('GITHUB_RUN_ID'),
                                    'interrupted': stopping})
        publish(root)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
