"""Evidence-backed retail auction identities, frozen inside each generation."""
import hashlib
import json
from pathlib import Path


class Registry:
    def __init__(self, document):
        if document.get('schema_version') != 1 or not document.get('version'):
            raise ValueError('Invalid auction market registry version')
        self.document = document
        self.payload = json.dumps(document, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
        self.sha256 = hashlib.sha256(self.payload.encode()).hexdigest()
        self.members = {}
        identities = set()
        for group in document['groups']:
            identity = group['auction_market_id']
            region = group['region_id']
            if (type(region) is not int or not 1 <= region <= 5 or not isinstance(identity, str)
                    or not identity.startswith('retail:%s:connected:' % region)):
                raise ValueError('Market identity must include its game version and region')
            if identity in identities or group['game_version'] != 'retail':
                raise ValueError('Duplicate market identity or unsupported game version')
            identities.add(identity)
            start, end = group['valid_from'], group.get('valid_to')
            if type(start) is not int or (end is not None and (type(end) is not int or end <= start)):
                raise ValueError('Invalid market validity interval')
            if not group.get('evidence') or not all(e.get('url', '').startswith('https://') for e in group['evidence']):
                raise ValueError('Market mappings require evidence URLs')
            realms = group['realms']
            ids = [r['id'] for r in realms]
            if (len(ids) < 2 or any(type(r) is not int or r <= 0 for r in ids)
                    or len(set(ids)) != len(ids) or group['representative_realm_id'] not in ids
                    or any(not isinstance(r.get('name'), str) or not r['name'] for r in realms)):
                raise ValueError('Invalid connected realm membership')
            for realm in realms:
                key = group['region_id'], realm['id']
                intervals = self.members.setdefault(key, [])
                if any(start < (g.get('valid_to') or float('inf')) and g['valid_from'] < (end or float('inf')) for g in intervals):
                    raise ValueError('Overlapping connected realm membership')
                intervals.append(group)

    def resolve(self, scan, *, timestamp=None):
        region, realm = scan['region_id'], scan['realm_id']
        captured = scan['timestamp'] if timestamp is None else timestamp
        group = next((g for g in self.members.get((region, realm), [])
                      if g['valid_from'] <= captured and (g.get('valid_to') is None or captured < g['valid_to'])), None)
        if group:
            return dict(auction_market_id=group['auction_market_id'],
                        auction_realm_id=group['representative_realm_id'],
                        auction_realm_name=' / '.join(r['name'] for r in group['realms']),
                        aliases=group['realms'], evidence=group['evidence'])
        # The interval suffix prevents history from crossing a connection and later split.
        ended = [g['valid_to'] for g in self.members.get((region, realm), [])
                 if g.get('valid_to') is not None and g['valid_to'] <= captured]
        suffix = ':since:%d' % max(ended) if ended else ''
        return dict(auction_market_id='retail:%s:realm:%s%s' % (region, realm, suffix),
                    auction_realm_id=realm, auction_realm_name=scan.get('realm_name', str(realm)),
                    aliases=[dict(id=realm, name=scan.get('realm_name', str(realm)))], evidence=[])

    def freeze(self, db):
        db.execute('CREATE TABLE IF NOT EXISTS auction_market_registry (payload VARCHAR)')
        db.execute('DELETE FROM auction_market_registry')
        db.execute('INSERT INTO auction_market_registry VALUES (?)', [self.payload])


def bundled_registry():
    return Registry(json.loads(Path(__file__).with_name('auction-markets.json').read_text(encoding='utf-8')))


def load_registry(db):
    exists = db.execute("SELECT count(*) FROM information_schema.tables WHERE table_name='auction_market_registry'").fetchone()[0]
    if not exists:
        # Legacy files have no reviewed identity mapping. Upgrade explicitly by staging
        # --market-registry; merely upgrading API code must not reinterpret live data.
        return Registry(dict(schema_version=1, version='legacy-independent', groups=[]))
    rows = db.execute('SELECT payload FROM auction_market_registry').fetchall()
    if len(rows) != 1:
        raise ValueError('Generation must contain exactly one market registry')
    return Registry(json.loads(rows[0][0]))
