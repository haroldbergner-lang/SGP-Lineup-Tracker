"""Verify read-only cloud connectivity; never log keys or quote references."""
import json
from pathlib import Path

from .combinations import candidate_pairs
from .fanduel import FanDuel, parse_quote, selections
from .lineups import fetch, get_json, parse_rotowire
from .watch import ET, dt
from datetime import datetime


def main():
    client = FanDuel()
    today = datetime.now(ET).date().isoformat()
    schedule = get_json('https://statsapi.mlb.com/api/v1/schedule?sportId=1&date=' + today)
    page = fetch('https://www.rotowire.com/baseball/daily-lineups.php?date=' + today)
    result = {'mlb_access': bool(schedule.get('dates')), 'rotowire_orders': len(parse_rotowire(page, today)),
              'fanduel_access': False, 'combined_quote_verified': False,
              'research_capture': False}
    events = client.events().get('attachments', {}).get('events', {})
    result['fanduel_access'] = bool(events)
    for eid, event in events.items():
        if ' @ ' not in event.get('name', ''):
            continue
        data = client.markets(str(eid))
        # Probe may inspect an in-play event solely to establish network/pricing
        # access. It is not a pregame research snapshot and is never exported.
        for market in data.get('attachments', {}).get('markets', {}).values():
            market['inPlay'] = False
        pair = next(candidate_pairs(selections(data, str(eid))), None)
        if not pair:
            continue
        response = client.quote(pair)
        parsed = parse_quote(response, pair)
        result['quote_status'] = parsed['status']
        if parsed['status'] == 'in_play':
            cleaned = dict(response)
            for key in ('runnerOdds', 'winRunnerOdds'):
                cleaned[key] = [{**r, 'inplay': False} for r in response.get(key, [])]
            parsed = parse_quote(cleaned, pair)
            result['pricing_probe_was_in_play'] = True
        result['combined_quote_verified'] = parsed['status'] == 'quoted'
        break
    Path('output').mkdir(exist_ok=True)
    Path('output/cloud-probe.json').write_text(json.dumps(result, indent=2))
    print(json.dumps(result, indent=2))
    return 0 if all((result['mlb_access'], result['rotowire_orders'],
                     result['fanduel_access'], result['combined_quote_verified'])) else 2


if __name__ == '__main__':
    raise SystemExit(main())
