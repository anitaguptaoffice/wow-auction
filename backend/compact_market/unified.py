"""Unified market identities backed by dictionary-encoded Arrow columns."""
from array import array
from collections import defaultdict
from pathlib import Path
import sys
import threading
import time

import duckdb
import pyarrow as pa
import pyarrow.compute as pc

from compact_market.store import MarketStore, SORTS, grams, rows_as_dicts, iso
from app.services.market_queries import SUPPORTED_COLLECTIONS


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
            for gram in set(name) | grams(name):
                self.search_index[gram].append(index)
            self.item_index[str(row['item_id'])].append(index)
            sort_values.append((row['min_unit_price'], row['total_quantity'], row['listing_count'], row['name'], key))
        self.sorted_ids = {}
        self.ranks = {}
        for scan_id, ids in self.by_scan.items():
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
                self.sorted_ids[scan_id, sort] = array('I', sorted(ids, key=order))
        self.scans = {key: value for key, value in self.scans.items() if key in self.by_scan}
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
        for sort in SORTS:
            self.sorted_ids[None, sort] = array('I', sorted(latest_ids, key=lambda i: self._sort_key(self.records[i], sort)))
        self.collections = {name: frozenset(i for i in range(len(self.records))
                            if self.records.columns['item_id'][i].as_py() in item_ids)
                            for name, item_ids in SUPPORTED_COLLECTIONS.items()}
        del sort_values
        for key, indices in self.history_index.items():
            self.history_index[key] = array('I', sorted(indices, key=lambda i: (
                self.scans[self.records.columns['scan_id'][i].as_py()]['timestamp'],
                self.records.columns['scan_id'][i].as_py())))
        self.default_scan = max(self.scans, key=lambda s: (self.scans[s]['timestamp'], -self.scans[s]['realm_id']))
        self.load_seconds = time.perf_counter() - started

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

    def items(self, *, scan_id=None, q='', sort='price_asc', page=1, page_size=20, collection=''):
        scan = self.scan(scan_id)
        if sort not in SORTS or page < 1 or not 1 <= page_size <= 100:
            raise ValueError('Invalid sort or pagination')
        ordered = self.sorted_ids[scan_id, sort]
        if collection:
            if collection not in self.collections:
                raise ValueError('Unknown collection')
            ordered = [i for i in ordered if i in self.collections[collection]]
        text = q.strip().casefold()
        if text:
            postings = sorted((self.search_index.get(g, ()) for g in grams(text)), key=len)
            matches = set(postings[0]) if postings else set()
            for posting in postings[1:]:
                matches.intersection_update(posting)
            matches = {i for i in matches if text in self.names[i]}
            matches.update(self.item_index.get(text, ()))
            ordered = [i for i in ordered if i in matches]
        total = len(ordered)
        start = (page - 1) * page_size
        return {'scanId': scan_id, 'page': page, 'pageSize': page_size,
                'total': total, 'totalPages': (total + page_size - 1) // page_size,
                'items': [self._public_record(self.records[i]) for i in ordered[start:start + page_size]]}

    def require_market(self, scan_id, market_key):
        scan = self.scan(scan_id)
        index = self.lookup.get((scan['scan_id'], market_key))
        if index is None:
            raise KeyError('Market item not found in selected scan')
        return scan, self.records[index]

    def history(self, *, market_key, scan_id=None):
        scan, _ = self.require_market(scan_id, market_key)
        indices = self.history_index[scan['region_id'], scan['realm_id'], market_key]
        points = []
        for index in indices:
            row = self.records[index]
            observed = self.scans[row['scan_id']]
            if observed['timestamp'] > scan['timestamp']:
                break
            points.append({'scan_id': row['scan_id'], 'timestamp': observed['timestamp'],
                           'realm_id': scan['realm_id'], 'region_id': scan['region_id'],
                           'source_scan_id': observed['source_scan_id'],
                           'source_realm': observed['source_realm_name'],
                           'min_buyout': row['min_buyout'], 'variant_count': row['variant_count'],
                           'min_unit_price': row['min_unit_price'], 'total_quantity': row['total_quantity'],
                           'listing_count': row['listing_count']})
        return {'marketKey': market_key, 'realmID': scan['realm_id'],
                'regionID': scan['region_id'], 'points': points}

    def public_history(self, *, market_key, scan_id=None):
        scan, row = self.require_market(scan_id, market_key)
        result = self._public_record(row)
        points = []
        for point in self.history(market_key=market_key, scan_id=scan_id)['points']:
            points.append({
                'scanId': point['scan_id'], 'scannedAt': iso(point['timestamp']),
                'scannedAtUnix': point['timestamp'], 'sourceDate': iso(point['timestamp'])[:10],
                'realm': scan['realm_name'], 'realmID': scan['realm_id'],
                'region': scan['region_name'], 'regionID': scan['region_id'],
                'marketScope': row['market_scope'], 'minUnitPrice': point['min_unit_price'],
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
        return {**result, 'points': points, 'pointCount': len(points), 'change': changes}
