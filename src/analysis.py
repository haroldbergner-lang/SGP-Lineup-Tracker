"""Rebuild analysis-ready CSVs from immutable manifests and incremental snapshots."""
import csv
import json
from pathlib import Path

from .lineups import normalize_name

STAGES = ('away_immediate', 'away_plus10', 'home_immediate', 'home_plus10', 'pregame')


def read_rows(path):
    try:
        with path.open(newline='', encoding='utf-8') as stream:
            return {r['combination_id']: r for r in csv.DictReader(stream) if r.get('combination_id')}
    except FileNotFoundError:
        return {}


def write_csv(path, rows):
    if not rows:
        return
    fields = list(dict.fromkeys(k for row in rows for k in row))
    temporary = path.with_suffix('.csv.tmp')
    with temporary.open('w', newline='', encoding='utf-8') as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    temporary.replace(path)


def player_fields(leg, state):
    name = normalize_name(leg['player'])
    matches = []
    for side in ('away', 'home'):
        all_players = state.get('projected', {}).get(side, {}).get('players', []) + state.get('confirmed', {}).get(side, {}).get('players', [])
        if any(normalize_name(p['name']) == name for p in all_players):
            matches.append(side)
    result = {'team_side': '', 'projected_position': '', 'confirmed_position': '',
              'position_change': '', 'projected_starter': '', 'confirmed_starter': '',
              'mlb_player_id': '', 'rotowire_player_id': '', 'name_match_status': 'unmatched'}
    if len(matches) != 1:
        result['name_match_status'] = 'ambiguous' if matches else 'unmatched'
        return result
    side = matches[0]
    result.update(team_side=side, name_match_status='full_name_accents_punctuation_suffix_normalized')
    for group, prefix in [('projected', 'projected'), ('confirmed', 'confirmed')]:
        order = state.get(group, {}).get(side)
        if not order:
            continue
        players = [p for p in order['players'] if normalize_name(p['name']) == name]
        result[prefix + '_starter'] = bool(players)
        if len(players) == 1:
            player = players[0]
            result[prefix + '_position'] = player['position']
            if group == 'confirmed':
                result['mlb_player_id'] = player.get('mlb_player_id', '')
            else:
                result['rotowire_player_id'] = player.get('source_player_id', '')
    if result['projected_position'] != '' and result['confirmed_position'] != '':
        result['position_change'] = result['confirmed_position'] - result['projected_position']
    return result


def export_all(root):
    root = Path(root)
    rows, lineup_rows = [], []
    for path in sorted(root.glob('*/manifest.json')):
        manifest = json.loads(path.read_text())
        state = json.loads((path.parent / 'state.json').read_text())
        eid = manifest['fd_event_id']
        snapshots = {stage: read_rows(path.parent / 'snapshots' / stage / 'quotes.csv')
                     for stage in ('baseline',) + STAGES}
        # Primary after is ten minutes after the second team's confirmation.
        confirmations = state.get('confirmed', {})
        primary = ''
        if len(confirmations) == 2:
            side = max(confirmations, key=lambda s: confirmations[s]['detected_at'])
            primary = side + '_plus10'
        for pair in manifest['pairs']:
            cid = eid + ':' + '|'.join(sorted(s['market_id'] + ':' + s['selection_id'] for s in pair))
            row = {'game_id': manifest['game_id'], 'fd_event_id': eid,
                   'scheduled_start': manifest['scheduled_start'], 'state': manifest['state'],
                   'away_team': manifest['teams']['away']['name'],
                   'home_team': manifest['teams']['home']['name'], 'combination_id': cid,
                   'baseline_due': state.get('stages', {}).get('baseline', {}).get('due', ''),
                   'projection_retrieved_at': state.get('baseline_observed_at', ''),
                   'baseline_already_confirmed': '|'.join(state.get('baseline_already_confirmed', []))}
            for number, leg in enumerate(pair, 1):
                for key in ('player', 'prop', 'team', 'market_id', 'selection_id', 'player_id', 'handicap', 'individual_odds'):
                    row[f'{key}_{number}'] = leg.get(key, '')
                for key, value in player_fields(leg, state).items():
                    row[f'{key}_{number}'] = value
            for side in ('away', 'home'):
                projected = state.get('projected', {}).get(side, {})
                confirmed = confirmations.get(side, {})
                row[side + '_projected_lineup'] = json.dumps(projected.get('players', []), ensure_ascii=False)
                row[side + '_actual_lineup'] = json.dumps(confirmed.get('players', []), ensure_ascii=False)
                row[side + '_projection_source'] = projected.get('source', '')
                row[side + '_confirmation_source'] = confirmed.get('source', '')
                row[side + '_lineup_detected_at'] = confirmed.get('detected_at', '')
                row[side + '_lineup_published_at'] = confirmed.get('source_published_at') or ''
                row[side + '_last_unconfirmed_at'] = confirmed.get('last_unconfirmed_at', '')
                row[side + '_lineup_revisions'] = json.dumps(confirmed.get('revisions', []), ensure_ascii=False)
            before = snapshots['baseline'].get(cid, {})
            after = snapshots.get(primary, {}).get(cid, {})
            row.update(odds_before=before.get('odds', '') if before.get('status') == 'quoted' else '',
                       odds_after=after.get('odds', '') if after.get('status') == 'quoted' else '',
                       probability_before=before.get('probability', '') if before.get('status') == 'quoted' else '',
                       probability_after=after.get('probability', '') if after.get('status') == 'quoted' else '',
                       timestamp_before=before.get('response_timestamp', ''), timestamp_after=after.get('response_timestamp', ''),
                       availability_before=before.get('status', 'pending'), availability_after=after.get('status', 'pending'),
                       quote_id_before=before.get('quote_id', ''), quote_id_after=after.get('quote_id', ''),
                       primary_after_stage=primary, probability_change='')
            row['baseline_observed_before_both_confirmations'] = bool(
                len(confirmations) == 2 and before.get('status') == 'quoted'
                and all(before.get('response_timestamp', 'z') < c['detected_at'] for c in confirmations.values()))
            row['baseline_no_later_than_last_unconfirmed_observations'] = bool(
                len(confirmations) == 2 and before.get('status') == 'quoted'
                and all(c.get('last_unconfirmed_at') and before['response_timestamp'] <= c['last_unconfirmed_at']
                        for c in confirmations.values()))
            row['timing_caveat'] = 'Detection is not publication; per-quote timestamps define the actual capture window'
            if before.get('status') == after.get('status') == 'quoted':
                row['probability_change'] = 100 * (float(after['probability']) - float(before['probability']))
            for stage in STAGES:
                quote = snapshots[stage].get(cid, {})
                for key in ('odds', 'probability', 'status', 'requested_due', 'request_timestamp', 'response_timestamp', 'raw_file'):
                    row[stage + '_' + key] = quote.get(key, 'pending' if key == 'status' else '')
            rows.append(row)
        # Include every player in either full order, even if absent from prop markets.
        for side in ('away', 'home'):
            players = state.get('projected', {}).get(side, {}).get('players', []) + confirmations.get(side, {}).get('players', [])
            names = {normalize_name(p['name']): p['name'] for p in players}
            for name in names.values():
                fields = player_fields({'player': name}, state)
                lineup_rows.append({'game_id': manifest['game_id'], 'team': manifest['teams'][side]['name'],
                                    'player': name, **fields,
                                    'projection_source': state.get('projected', {}).get(side, {}).get('source', ''),
                                    'projection_retrieved_at': state.get('baseline_observed_at', ''),
                                    'lineup_detected_at': confirmations.get(side, {}).get('detected_at', '')})
    write_csv(root / 'sgp_price_changes.csv', rows)
    write_csv(root / 'lineup_changes.csv', lineup_rows)
    return len(rows)
