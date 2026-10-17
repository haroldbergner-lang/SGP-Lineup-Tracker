import copy
import unittest

from src.fanduel import parse_quote, quote_body, selections
from src.combinations import candidate_pairs

PAIR = [{'market_id': '1.1', 'selection_id': '10'},
        {'market_id': '1.2', 'selection_id': '20'}]


def response():
    return {'respCode': 'SUCCESS', 'betCombinations': [
        {'isSGM': True, 'betType': 'DOUBLE', 'betReference': 'fixture-reference',
         'legCombinations': [{'runners': [{'marketId': s['market_id'],
                                         'selectionId': int(s['selection_id'])} for s in PAIR]}],
         'winAvgOdds': {'trueOdds': {'decimalOdds': {'decimalOdds': 4}},
                        'americanDisplayOdds': {'americanOdds': 300}}}]}


class Quotes(unittest.TestCase):
    def test_exact_sgp(self):
        self.assertEqual(parse_quote(response(), PAIR)['probability'], .25)

    def test_reject_singles_or_ordinary_double(self):
        for kind, sgm in [('SINGLE', False), ('DOUBLE', False), ('TREBLE', True)]:
            data = response()
            data['betCombinations'][0].update(betType=kind, isSGM=sgm)
            self.assertEqual(parse_quote(data, PAIR)['status'], 'no_verified_sgp')

    def test_reject_wrong_or_extra_runner(self):
        for runners in [[{'marketId': '1.1', 'selectionId': 99}],
                        [{'marketId': s['market_id'], 'selectionId': s['selection_id']} for s in PAIR] * 2]:
            data = response()
            data['betCombinations'][0]['legCombinations'] = [{'runners': runners}]
            self.assertEqual(parse_quote(data, PAIR)['status'], 'no_verified_sgp')

    def test_reject_bad_prices(self):
        for price in [0, 1, float('nan'), float('inf'), 10]:
            data = response()
            data['betCombinations'][0]['winAvgOdds']['trueOdds']['decimalOdds']['decimalOdds'] = price
            self.assertEqual(parse_quote(data, PAIR)['status'], 'no_verified_sgp')

    def test_failure_preserved(self):
        data = {'respCode': 'SUCCESS', 'betFailures': [{'failureCode': 'INVALID_COMBINATION'}]}
        self.assertEqual(parse_quote(data, PAIR)['failure_codes'], 'INVALID_COMBINATION')

    def test_live_response_rejected(self):
        data = response()
        data['runnerOdds'] = [{'inplay': True}]
        self.assertEqual(parse_quote(data, PAIR)['status'], 'in_play')

    def test_request_exact_ids(self):
        self.assertEqual(quote_body(PAIR)['betLegs'][1]['betRunners'][0]['runner'],
                         {'marketId': '1.2', 'selectionId': 20})

    def test_both_run_rbi_directions(self):
        legs = [{'team': 'rays', 'player': name, 'prop': prop}
                for name in ['Alex', 'Ben'] for prop in ['1+ run', '1+ RBI']]
        pairs = list(candidate_pairs(legs))
        self.assertEqual({(a['player'], b['player']) for a, b in pairs}, {('Alex', 'Ben'), ('Ben', 'Alex')})
        self.assertTrue(all(a['prop'] == '1+ run' and b['prop'] == '1+ RBI' for a, b in pairs))

    def test_opponents_unknown_teams_and_other_props_excluded(self):
        legs = [{'team': 'rays', 'player': 'Alex', 'prop': '1+ run'},
                {'team': 'guardians', 'player': 'Ben', 'prop': '1+ RBI'},
                {'team': '', 'player': 'Carl', 'prop': '1+ RBI'},
                {'team': 'rays', 'player': 'Dan', 'prop': '1+ hit'}]
        self.assertEqual(list(candidate_pairs(legs)), [])

    def test_same_player_suffix_or_accent_excluded(self):
        for first, second in [('Yandy Díaz', 'Yandy Diaz'), ('Victor Mesa', 'Victor Mesa Jr.')]:
            legs = [{'team': 'rays', 'player': first, 'prop': '1+ run'},
                    {'team': 'rays', 'player': second, 'prop': '1+ RBI'}]
            self.assertEqual(list(candidate_pairs(legs)), [])

    def test_market_filter(self):
        market = {'eventId': 100, 'sgmMarket': True, 'marketStatus': 'OPEN',
                  'marketType': 'TO_RECORD_A_RUN', 'runners': [
                      {'selectionId': 10, 'runnerName': 'Player',
                       'isPlayerSelection': True, 'runnerStatus': 'ACTIVE'}]}
        def extract(m):
            return selections({'attachments': {'markets': {'1.1': m}}}, '100')
        self.assertEqual(extract(market)[0]['prop'], '1+ run')
        for changes in [{'eventId': 101}, {'sgmMarket': False}, {'marketStatus': 'SUSPENDED'},
                        {'inPlay': True}, {'marketType': 'TO_RECORD_2+_RUNS'}]:
            modified = copy.deepcopy(market)
            modified.update(changes)
            self.assertEqual(extract(modified), [])


if __name__ == '__main__':
    unittest.main()
