"""Unified market identities backed by dictionary-encoded Arrow columns."""
from array import array
from collections import defaultdict, OrderedDict
from pathlib import Path
import sys
import threading
import time

import duckdb
import pyarrow as pa
import pyarrow.compute as pc

from compact_market.store import MarketStore, SORTS, grams, rows_as_dicts, iso
from app.services.market_queries import SUPPORTED_COLLECTIONS, RAID_BOE_12_1_ITEM_IDS, _parse_item_variant
from compact_market.variant_filters import VariantFilters
from compact_market.history import sample_points


class ArrowRows:
    """Decode only requested rows; avoid a Python dictionary per stored observation."""
    def __init__(self, table):
        self.table = table.combine_chunks()
        self.columns = {name: self.table[name].chunk(0) for name in self.table.column_names}

    def __len__(self):
        return self.table.num_rows

    def __getitem__(self, index):
        return {name: column[index].as_py() for name, column in self.columns.items()}


class UnifiedMarketStore(MarketStore):
    def __init__(self, database: Path, *, threads=2, memory_limit="256MB"):
        started = time.perf_counter()
        self.database = database
        self.root = duckdb.connect(str(database), read_only=True,
                                   config={"threads": threads, "memory_limit": memory_limit})
        self.local = threading.local()
        self.connection_lock = threading.Lock()
        self.connections = []
        originals = rows_as_dicts(self.root.execute("SELECT * FROM scans"))
        self.scans = {}
        for source in originals:
            if source['region_id'] is None or source['realm_id'] is None:
                raise ValueError('Unified markets require captured region and realm IDs')
            for regional in (True, False):
                scan = dict(source)
                scan['source_scan_id'] = source['scan_id']
                scan['source_realm_name'] = source['realm_name']
                scan['scan_id'] = source['scan_id'] * 2 + (0 if regional else 1)
                if regional:
                    scan['realm_id'] = 0
                    scan['realm_name'] = '全服'
                self.scans[scan['scan_id']] = scan
        table = self.root.execute("""
            SELECT s.* EXCLUDE(scan_id),
                   s.scan_id * 2 + CASE WHEN p.market_scope='region' THEN 0 ELSE 1 END AS scan_id,
                   coalesce(p.market_scope,'unknown') AS market_scope
            FROM summaries s LEFT JOIN scopes p ON p.scan_id=s.scan_id AND p.item_id=s.item_id
            ORDER BY scan_id, s.market_key
        """).to_arrow_table()
        for i, field in enumerate(table.schema):
            if pa.types.is_string(field.type):
                table = table.set_column(i, field.name, pc.dictionary_encode(table.column(i)))
        self.records = ArrowRows(table)
        self.by_scan = defaultdict(lambda: array('I'))
        self.search_index = defaultdict(lambda: array('I'))
        self.item_index = defaultdict(lambda: array('I'))
        self.names = []
        self.lookup = {}
        self.history_index = defaultdict(lambda: array('I'))
        sort_values = []
        for index in range(len(self.records)):
            row = self.records[index]
            scan_id = row['scan_id']
            self.by_scan[scan_id].append(index)
            key = sys.intern(row['market_key'])
            self.lookup[scan_id, key] = index
            scan = self.scans[scan_id]
            self.history_index[scan['region_id'], scan['realm_id'], key].append(index)
            name = sys.intern(row['name'].casefold())
            self.names.append(name)
            sort_values.append((row['min_unit_price'], row['total_quantity'], row['listing_count'], row['name'], key))
        self.sorted_ids = {}
        self.ranks = {}
        # A complete empty market is still a valid observation (all goods absent).
        for scan_id, scan in self.scans.items():
            scan['record_count'] = sum(sort_values[i][2] for i in self.by_scan[scan_id])
            scan['total_quantity'] = sum(sort_values[i][1] for i in self.by_scan[scan_id])
        latest = {}
        for scan_id, scan in self.scans.items():
            identity = (scan['region_id'], scan['realm_id'])
            previous = latest.get(identity)
            if previous is None or (scan['timestamp'], scan_id) > (self.scans[previous]['timestamp'], previous):
                latest[identity] = scan_id
        self.latest_scans = tuple(latest.values())
        latest_ids = [i for sid in self.latest_scans for i in self.by_scan[sid]]
        for index in latest_ids:
            for gram in set(self.names[index]) | grams(self.names[index]):
                self.search_index[gram].append(index)
            self.item_index[str(self.records.columns['item_id'][index].as_py())].append(index)
        self.historical_sorts = OrderedDict()
        self.sort_lock = threading.Lock()
        for sort in SORTS:
            def order(index):
                price, quantity, count, name, key = sort_values[index]
                if sort.startswith('price_'):
                    return price is None, (price or 0) * (-1 if sort == 'price_desc' else 1), key
                if sort == 'quantity_desc':
                    return -quantity, key
                if sort == 'listings_desc':
                    return -count, key
                return name, key
            for sid in self.latest_scans:
                self.sorted_ids[sid, sort] = array('I', sorted(self.by_scan[sid], key=order))
            self.sorted_ids[None, sort] = array('I', sorted(latest_ids, key=order))
        self.collections = {name: frozenset(i for i in range(len(self.records))
                            if self.records.columns['item_id'][i].as_py() in item_ids)
                            for name, item_ids in SUPPORTED_COLLECTIONS.items()}
        # This small collection is decoded once per immutable generation. Filtering
        # its descriptor aggregates avoids scanning millions of auction facts per click.
        self.raid_descriptors = {}
        for row in rows_as_dicts(self.root.execute(
                'SELECT descriptor_id, item_link FROM descriptors WHERE item_id IN ('
                + ','.join('?' for _ in RAID_BOE_12_1_ITEM_IDS) + ')', list(RAID_BOE_12_1_ITEM_IDS))):
            self.raid_descriptors[row['descriptor_id']] = _parse_item_variant(row['item_link'])
        self.raid_facts = defaultdict(list)
        for row in rows_as_dicts(self.root.execute('''
            SELECT f.scan_id, d.market_key, f.descriptor_id,
                   count(*) AS listing_count, sum(f.quantity) AS total_quantity,
                   min(f.unit_price) AS min_unit_price,
                   min(nullif(f.buyout_amount, 0)) AS min_buyout
            FROM listings f JOIN descriptors d USING(descriptor_id)
            WHERE d.item_id IN (''' + ','.join('?' for _ in RAID_BOE_12_1_ITEM_IDS) + ''')
            GROUP BY f.scan_id, d.market_key, f.descriptor_id
        ''', list(RAID_BOE_12_1_ITEM_IDS))):
            self.raid_facts[row['scan_id'], row['market_key']].append(row)
        del sort_values
        for key, indices in self.history_index.items():
            self.history_index[key] = array('I', sorted(indices, key=lambda i: (
                self.scans[self.records.columns['scan_id'][i].as_py()]['timestamp'],
                self.records.columns['scan_id'][i].as_py())))
        self.default_scan = max(self.scans, key=lambda s: (self.scans[s]['timestamp'], -self.scans[s]['realm_id']))
        self.load_seconds = time.perf_counter() - started

    def _stat_combination_count(self, facts):
        # Link bonuses, sockets and tertiary attributes do not create a new
        # secondary-stat pair. Preserve the captured primary/secondary order.
        return len({self.raid_descriptors[f['descriptor_id']]['statKey'] for f in facts
                    if self.raid_descriptors[f['descriptor_id']]['statKey'] is not None})

    def _public_record(self, row):
        result = super()._public_record(row)
        if row['item_id'] in RAID_BOE_12_1_ITEM_IDS:
            if 'stat_combination_count' in row:
                count = row['stat_combination_count']
            else:
                source = self.scans[row['scan_id']]['source_scan_id']
                count = self._stat_combination_count(self.raid_facts[source, row['market_key']]) if row['listing_count'] else 0
            result['statCombinationCount'] = count
        return result

    def ordered_ids(self, scan_id, sort):
        key = (scan_id, sort)
        if key in self.sorted_ids:
            return self.sorted_ids[key]
        with self.sort_lock:
            if key not in self.historical_sorts:
                fields = ('market_key', 'min_unit_price' if sort.startswith('price_') else
                          'total_quantity' if sort == 'quantity_desc' else 'listing_count' if sort == 'listings_desc' else 'name')
                def order(index):
                    row = {field: self.records.columns[field][index].as_py() for field in fields}
                    return self._sort_key(row, sort)
                self.historical_sorts[key] = array('I', sorted(self.by_scan[scan_id], key=order))
                if len(self.historical_sorts) > 16:
                    self.historical_sorts.popitem(last=False)
            self.historical_sorts.move_to_end(key)
            return self.historical_sorts[key]

    def status(self, scan_id=None):
        result = super().status(scan_id)
        result.update(engine='unified-arrow', historyColumnBytes=self.records.table.nbytes,
                      sourceRealm=self.scan(scan_id)['source_realm_name'])
        if scan_id is None:
            result.update(scanId=None, realm=None, realmID=None,
                          listingCount=sum(self.scans[s]['record_count'] for s in self.latest_scans),
                          totalQuantity=sum(self.scans[s]['total_quantity'] for s in self.latest_scans),
                          marketItemCount=len(self.sorted_ids[None, 'price_asc']))
        return result

    def items(self, *, scan_id=None, q='', sort='price_asc', page=1, page_size=20, collection='',
              difficulty='', stats='', socket='', tertiary=''):
        scan = self.scan(scan_id)
        filters = VariantFilters(difficulty, stats, socket, tertiary)
        if filters.active and collection != 'raid_boe_12_1':
            raise ValueError('Equipment filters require the raid collection')
        if sort not in SORTS or page < 1 or not 1 <= page_size <= 100:
            raise ValueError('Invalid sort or pagination')
        ordered = self.ordered_ids(scan_id, sort)
        if collection:
            if collection not in self.collections:
                raise ValueError('Unknown collection')
            ordered = [i for i in ordered if i in self.collections[collection]]
        text = q.strip().casefold()
        if text and scan_id is not None and scan_id not in self.latest_scans:
            ordered = [i for i in ordered if text in self.names[i]
                       or text == str(self.records.columns['item_id'][i].as_py())]
        elif text:
            postings = sorted((self.search_index.get(g, ()) for g in grams(text)), key=len)
            matches = set(postings[0]) if postings else set()
            for posting in postings[1:]:
                matches.intersection_update(posting)
            matches = {i for i in matches if text in self.names[i]}
            matches.update(self.item_index.get(text, ()))
            ordered = [i for i in ordered if i in matches]
        if filters.active:
            matched = []
            for i in ordered:
                row = dict(self.records[i])
                source_scan = self.scans[row['scan_id']]['source_scan_id']
                facts = [f for f in self.raid_facts[source_scan, row['market_key']]
                         if filters.matches(self.raid_descriptors[f['descriptor_id']])]
                if not facts:
                    continue
                row.update(listing_count=sum(f['listing_count'] for f in facts),
                           total_quantity=sum(f['total_quantity'] for f in facts),
                           variant_count=len(facts),
                           stat_combination_count=self._stat_combination_count(facts),
                           min_unit_price=min((f['min_unit_price'] for f in facts if f['min_unit_price'] is not None), default=None),
                           min_buyout=min((f['min_buyout'] for f in facts if f['min_buyout'] is not None), default=None))
                matched.append(row)
            matched.sort(key=lambda row: self._sort_key(row, sort))
        else:
            matched = None
        total = len(matched) if matched is not None else len(ordered)
        start = (page - 1) * page_size
        page_rows = matched[start:start + page_size] if matched is not None else [self.records[i] for i in ordered[start:start + page_size]]
        public_rows = [self._public_record(row) for row in page_rows]
        if filters.active:
            for row in public_rows:
                row['variantFilters'] = filters.public()
        return {'scanId': scan_id, 'page': page, 'pageSize': page_size,
                'total': total, 'totalPages': (total + page_size - 1) // page_size,
                'items': public_rows}

    def require_market(self, scan_id, market_key):
        scan = self.scan(scan_id)
        index = self.lookup.get((scan['scan_id'], market_key))
        if index is None:
            raise KeyError('Market item not found in selected scan')
        return scan, self.records[index]

    def history(self, *, market_key, scan_id=None, difficulty='', stats='', socket='', tertiary=''):
        scan, current = self.require_market(scan_id, market_key)
        filters = VariantFilters(difficulty, stats, socket, tertiary)
        if filters.active and current['item_id'] not in RAID_BOE_12_1_ITEM_IDS:
            raise ValueError('Equipment history filters require raid equipment')
        indices = self.history_index[scan['region_id'], scan['realm_id'], market_key]
        first_time = self.scans[self.records[indices[0]]['scan_id']]['timestamp']
        timeline = sorted((s for s in self.scans.values()
                           if (s['region_id'], s['realm_id']) == (scan['region_id'], scan['realm_id'])
                           and first_time <= s['timestamp'] <= scan['timestamp']),
                          key=lambda s: (s['timestamp'], s['scan_id']))
        points = []
        for observed in timeline:
            index = self.lookup.get((observed['scan_id'], market_key))
            row = dict(self.records[index]) if index is not None else None
            if row and filters.active:
                facts = [f for f in self.raid_facts[observed['source_scan_id'], market_key]
                         if filters.matches(self.raid_descriptors[f['descriptor_id']])]
                row.update(listing_count=sum(f['listing_count'] for f in facts),
                           total_quantity=sum(f['total_quantity'] for f in facts), variant_count=len(facts),
                           min_unit_price=min((f['min_unit_price'] for f in facts if f['min_unit_price'] is not None), default=None),
                           min_buyout=min((f['min_buyout'] for f in facts if f['min_buyout'] is not None), default=None))
            present = bool(row and row['listing_count'])
            points.append({'scan_id': observed['scan_id'], 'timestamp': observed['timestamp'],
                           'realm_id': scan['realm_id'], 'region_id': scan['region_id'],
                           'source_scan_id': observed['source_scan_id'],
                           'source_realm': observed['source_realm_name'],
                            'state': 'observed' if present and row['min_unit_price'] is not None else 'no_price' if present else 'absent',
                            'min_buyout': row['min_buyout'] if present else None,
                            'variant_count': row['variant_count'] if present else 0,
                            'min_unit_price': row['min_unit_price'] if present else None,
                            'total_quantity': row['total_quantity'] if present else 0,
                            'listing_count': row['listing_count'] if present else 0})
        return {'marketKey': market_key, 'realmID': scan['realm_id'],
                'regionID': scan['region_id'], 'points': points}

    def public_history(self, *, market_key, scan_id=None, start=None, end=None, max_points=400,
                       gap_seconds=0, difficulty='', stats='', socket='', tertiary=''):
        if start is not None and end is not None and start > end:
            raise ValueError('History start must not exceed end')
        scan, row = self.require_market(scan_id, market_key)
        result = self._public_record(row)
        points = []
        for point in self.history(market_key=market_key, scan_id=scan_id, difficulty=difficulty,
                                  stats=stats, socket=socket, tertiary=tertiary)['points']:
            if (start is not None and point['timestamp'] < start) or (end is not None and point['timestamp'] > end):
                continue
            points.append({
                'scanId': point['scan_id'], 'scannedAt': iso(point['timestamp']),
                'scannedAtUnix': point['timestamp'], 'sourceDate': iso(point['timestamp'])[:10],
                'realm': scan['realm_name'], 'realmID': scan['realm_id'],
                'region': scan['region_name'], 'regionID': scan['region_id'],
                'marketScope': row['market_scope'], 'minUnitPrice': point['min_unit_price'],
                'state': point['state'],
                'minBuyout': point['min_buyout'], 'totalQuantity': point['total_quantity'],
                'listingCount': point['listing_count'], 'variantCount': point['variant_count'],
            })
        changes = {}
        for metric in ('minUnitPrice', 'minBuyout', 'listingCount', 'totalQuantity'):
            changes[metric] = None
            if len(points) >= 2:
                previous, current = points[-2][metric], points[-1][metric]
                if previous is not None and current is not None:
                    changes[metric] = dict(previous=previous, current=current, absolute=current-previous,
                                           percent=(current-previous)/previous*100 if previous else None)
        return {**result, 'points': sample_points(points, max_points, gap_seconds),
                'pointCount': len(points), 'change': changes,
                'variantFilters': VariantFilters(difficulty, stats, socket, tertiary).public()}

    def compare(self, *, market_key, scan_id=None, difficulty='', stats='', socket='', tertiary=''):
        filters = VariantFilters(difficulty, stats, socket, tertiary)
        prototype = self.require_market(scan_id, market_key)[1] if scan_id is not None else next(
            (self.records[i] for (sid, key), i in self.lookup.items() if key == market_key), None)
        if prototype is None:
            raise KeyError('Item not found')
        source = self.scans[prototype['scan_id']]
        rows = []
        for sid in self.latest_scans:
            scan = self.scans[sid]
            if scan['region_id'] != source['region_id'] or (scan['realm_id'] == 0) != (source['realm_id'] == 0):
                continue
            index = self.lookup.get((sid, market_key))
            item = self._public_record(self.records[index]) if index is not None else None
            if item and filters.active:
                candidates = self.items(scan_id=sid, collection='raid_boe_12_1', q=str(item['itemID']),
                                        page_size=100, **filters.public())['items']
                item = next((c for c in candidates if c['marketKey'] == market_key), None)
            if item is None:
                empty = dict(prototype, scan_id=sid, min_unit_price=None, min_buyout=None,
                             listing_count=0, variant_count=0, total_quantity=0)
                item = self._public_record(empty)
                item['variantFilters'] = filters.public() if filters.active else None
            item['availability'] = 'observed' if item['listingCount'] else 'absent'
            rows.append(item)
        return {'items': sorted(rows, key=lambda r: (r['minUnitPrice'] is None, r['minUnitPrice'] or 0)),
                'variantFilters': filters.public()}
