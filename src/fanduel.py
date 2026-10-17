"""Read-only market/quote adapter adapted from kalshi-sgp-pricer/fd_api_pricer.py.

FanDuel's undocumented interface can change. Never estimate missing SGP odds.
"""
import json
import math
import os
import re
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

MARKETS = {
    'PLAYER_TO_RECORD_A_HIT': '1+ hit',
    'TO_RECORD_A_RUN': '1+ run',
    'TO_RECORD_AN_RBI': '1+ RBI',
    'TO_RECORD_2+_TOTAL_BASES': '2+ total bases',
}


def selections(data, event_id):
    """Use explicit market types and IDs; no names or thresholds are guessed."""
    result = {}
    for mid, market in data.get('attachments', {}).get('markets', {}).items():
        prop = MARKETS.get(market.get('marketType'))
        if (not prop or str(market.get('eventId')) != str(event_id)
                or market.get('sgmMarket') is not True
                or market.get('marketStatus') != 'OPEN' or market.get('inPlay')):
            continue
        for runner in market.get('runners', []):
            if runner.get('runnerStatus') != 'ACTIVE' or runner.get('isPlayerSelection') is not True:
                continue
            sid = runner.get('selectionId')
            if sid is None:
                continue
            key = (str(mid), str(sid))
            result[key] = {
                'market_id': str(mid), 'selection_id': str(sid),
                'player': runner['runnerName'], 'prop': prop,
                # Selection IDs are not asserted to be MLB or FanDuel player IDs.
                'player_id': runner.get('playerId', ''),
                'handicap': runner.get('handicap'),
                'individual_odds': runner.get('winRunnerOdds', {}).get(
                    'americanDisplayOdds', {}).get('americanOdds', ''),
            }
    return [result[k] for k in sorted(result)]


def quote_body(pair):
    return {'betLegs': [
        {'legType': 'SIMPLE_SELECTION', 'betRunners': [
            {'runner': {'marketId': leg['market_id'],
                        'selectionId': int(leg['selection_id'])}}]}
        for leg in pair]}


def parse_quote(data, pair):
    """Require one SGP double containing exactly the two requested runners."""
    expected = {(s['market_id'], str(s['selection_id'])) for s in pair}
    if len(expected) != 2:
        raise ValueError('Exactly two distinct selections are required')
    if data.get('respCode') != 'SUCCESS':
        return {'status': 'response_error'}
    if any(r.get('inplay') is True for key in ('runnerOdds', 'winRunnerOdds')
           for r in data.get(key, [])):
        return {'status': 'in_play'}
    for combo in data.get('betCombinations', []):
        if combo.get('isSGM') is not True or combo.get('betType') != 'DOUBLE':
            continue
        runners = [r for leg in combo.get('legCombinations', [])
                   for r in leg.get('runners', [])]
        actual = {(str(r.get('marketId')), str(r.get('selectionId'))) for r in runners}
        if len(runners) != 2 or actual != expected:
            continue
        odds = combo.get('winAvgOdds', {})
        try:
            decimal = float(odds['trueOdds']['decimalOdds']['decimalOdds'])
            american = float(odds['americanDisplayOdds']['americanOdds'])
        except (KeyError, TypeError, ValueError):
            continue
        if not math.isfinite(decimal) or decimal <= 1 or not math.isfinite(american) or abs(american) < 100:
            continue
        probability = 100 / (american + 100) if american > 0 else -american / (-american + 100)
        if abs(probability - 1 / decimal) > .005:
            continue
        return {'status': 'quoted', 'odds': american, 'decimal_odds': decimal,
                'probability': 1 / decimal, 'quote_id': combo.get('betReference', '')}
    codes = sorted({str(f.get('failureCode', 'UNKNOWN'))
                    for key in ('betFailures', 'legFailures') for f in data.get(key, [])})
    return {'status': 'no_verified_sgp', 'failure_codes': '|'.join(codes)}


class FanDuel:
    def __init__(self):
        self.state = os.environ.get('FD_STATE', 'nj')
        if not re.fullmatch('[a-z]{2}', self.state):
            raise ValueError('FD_STATE must be a two-letter state code')
        self.key = os.environ.get('FD_API_KEY', '')
        if not self.key:
            raise ValueError('Set FD_API_KEY to the frontend key used by your existing scraper')
        self.base = f'https://sbapi.{self.state}.sportsbook.fanduel.com/api'
        self.params = {'_ak': self.key, 'betexRegion': 'GBR', 'capiJurisdiction': 'intl',
                       'currencyCode': 'USD', 'exchangeLocale': 'en_US',
                       'language': 'en', 'regionCode': 'NAMERICA'}

    def request(self, url, body=None):
        headers = {'User-Agent': 'Mozilla/5.0', 'Accept': 'application/json',
                   'Origin': 'https://sportsbook.fanduel.com',
                   'Referer': 'https://sportsbook.fanduel.com/'}
        if body is not None:
            headers['Content-Type'] = 'application/json'
            headers['X-Application'] = self.key
        request = Request(url, data=None if body is None else json.dumps(body).encode(), headers=headers)
        try:
            with urlopen(request, timeout=20) as response:
                return json.load(response)
        except HTTPError as error:
            # Never include the URL/API key or response tokens in diagnostics.
            raise RuntimeError(f'FanDuel HTTP {error.code}') from None

    def events(self):
        return self.request(self.base + '/content-managed-page?' + urlencode(
            {**self.params, 'page': 'CUSTOM', 'customPageId': 'mlb'}))

    def markets(self, event_id):
        return self.request(self.base + '/event-page?' + urlencode(
            {**self.params, 'eventId': event_id, 'tab': 'batter-props'}))

    def quote(self, pair):
        return self.request(f'https://sib.{self.state}.sportsbook.fanduel.com'
                            '/api/sports/fixedodds/transactional/v1/implyBets'
                            '?scope=PREVIEW&pricePolicy=SUGGESTED', quote_body(pair))
