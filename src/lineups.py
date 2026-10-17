"""Public RotoWire expected/confirmed orders and MLB schedule/boxscore reads."""
from dataclasses import dataclass, field
from html.parser import HTMLParser
import json
import re
import unicodedata
from urllib.request import Request, urlopen


def fetch(url):
    with urlopen(Request(url, headers={'User-Agent': 'Mozilla/5.0',
                                      'Cache-Control': 'no-cache'}), timeout=20) as response:
        return response.read().decode('utf-8')


def get_json(url):
    return json.loads(fetch(url))


@dataclass
class Node:
    tag: str
    attrs: dict = field(default_factory=dict)
    children: list = field(default_factory=list)
    text: str = ''

    def has(self, name):
        return name in self.attrs.get('class', '').split()

    def find(self, name):
        result = [self] if self.has(name) else []
        for child in self.children:
            result.extend(child.find(name))
        return result

    def all_text(self):
        return ' '.join([self.text] + [c.all_text() for c in self.children]).strip()

    def tags(self, tag):
        result = [self] if self.tag == tag else []
        for child in self.children:
            result.extend(child.tags(tag))
        return result


class Document(HTMLParser):
    def __init__(self, html):
        super().__init__(convert_charrefs=True)
        self.root = Node('root')
        self.stack = [self.root]
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        node = Node(tag, dict(attrs))
        self.stack[-1].children.append(node)
        if tag not in {'area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input',
                       'link', 'meta', 'param', 'source', 'track', 'wbr'}:
            self.stack.append(node)

    def handle_endtag(self, tag):
        for index in range(len(self.stack) - 1, 0, -1):
            if self.stack[index].tag == tag:
                self.stack = self.stack[:index]
                break

    def handle_data(self, data):
        self.stack[-1].text += data


def parse_rotowire(html, date):
    """Return orders keyed by (away abbreviation, home abbreviation).

    Only explicit expected/confirmed status and complete, distinct nine-player
    orders are accepted. A shape change fails closed rather than guessing.
    """
    games = {}
    for card in Document(html).root.find('lineup'):
        if card.has('is-tools'):
            continue
        links = card.find('lineup__matchup')
        if not links or f'-{date}-' not in links[0].attrs.get('href', ''):
            continue
        teams = [n.all_text() for n in card.find('lineup__abbr')]
        if len(teams) != 2:
            continue
        orders = {}
        for group in card.find('lineup__list'):
            side = 'away' if group.has('is-visit') else 'home' if group.has('is-home') else None
            statuses = group.find('lineup__status')
            if not side or len(statuses) != 1:
                continue
            status = ('confirmed' if statuses[0].has('is-confirmed') else
                      'expected' if statuses[0].has('is-expected') else 'unknown')
            if status == 'unknown':
                continue
            players = []
            for number, row in enumerate(group.find('lineup__player'), 1):
                anchors = row.tags('a')
                if len(anchors) != 1:
                    continue
                anchor = anchors[0]
                match = re.search(r'/baseball/player/[^/]+-(\d+)$', anchor.attrs.get('href', ''))
                if not match or not anchor.attrs.get('title'):
                    continue
                players.append({'name': anchor.attrs['title'], 'source_player_id': match[1],
                                'position': number, 'starter': True})
            if len(players) == 9 and len({p['source_player_id'] for p in players}) == 9:
                orders[side] = {'status': status, 'players': players, 'source': 'RotoWire public page'}
        if orders:
            key = tuple(teams)
            if key in games:
                raise ValueError('Ambiguous RotoWire matchup (possible doubleheader)')
            games[key] = orders
    return games


def normalize_name(name):
    name = unicodedata.normalize('NFKD', name).encode('ascii', 'ignore').decode().casefold()
    name = re.sub(r'\b(jr|sr|ii|iii|iv)\.?\s*$', '', name).strip()
    return re.sub(r'[^a-z0-9]', '', name)


def official_order(data, side):
    """Use MLB IDs/order only; complete nine-player pregame order is the published MLB order."""
    team = data.get('teams', {}).get(side, {})
    ids = team.get('battingOrder', [])
    if len(ids) != 9 or len(set(ids)) != 9:
        return None
    players = team.get('players', {})
    result = []
    for position, pid in enumerate(ids, 1):
        person = players.get('ID' + str(pid), {}).get('person', {})
        if not person.get('fullName'):
            return None
        result.append({'name': person['fullName'], 'mlb_player_id': str(pid),
                       'position': position, 'starter': True})
    return result
