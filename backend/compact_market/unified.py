"""Unified market identities backed by dictionary-encoded Arrow columns."""
from array import array
from bisect import bisect_left, bisect_right
from collections import defaultdict, OrderedDict
from dataclasses import dataclass
from pathlib import Path
import sys
import threading
import time

import duckdb
import pyarrow as pa
import pyarrow.compute as pc

from compact_market.store import MarketStore, SORTS, PUBLIC_LISTINGS_SQL, grams, rows_as_dicts, iso
from compact_market.schema import RESTORE_SQL
from app.services.market_queries import SUPPORTED_COLLECTIONS, RAID_BOE_12_1_ITEM_IDS, _parse_item_variant
from compact_market.variant_filters import VariantFilters


class ArrowRows:
    """Decode only requested rows; avoid a Python dictionary per stored observation."""
    def __init__(self, table):
        self.table = table.combine_chunks()
        self.columns = {name: self.table[name].chunk(0) for name in self.table.column_names}

    def __len__(self):
        return self.table.num_rows

    def __getitem__(self, index):
        return {name: column[index].as_py() for name, column in self.columns.items()}


@dataclass(frozen=True, slots=True)
class MarketGroup:
    indices: tuple
    winner: int
    buyout_winner: int
    listing_count: int
    total_quantity: int
    stat_count: int | None


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
        self.scan_timelines = defaultdict(list)
        for sid, observed in self.scans.items():
            self.scan_timelines[observed['region_id'], observed['realm_id']].append((observed['timestamp'], sid))
        for timeline in self.scan_timelines.values():
            timeline.sort()
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
        buckets = defaultdict(list)
        for (sid, key), index in self.lookup.items():
            if sid not in self.latest_scans:
                continue
            scope = self.records.columns['market_scope'][index].as_py()
            scan = self.scans[sid]
            identity = (scan['region_id'], scope, scan['realm_id'] if scope == 'unknown' else None, key)
            buckets[identity].append(index)
        self.group_id_by_record = array('i', [-1]) * len(self.records)
        self.market_groups = []
        for indices in buckets.values():
            gid = len(self.market_groups)
            self.market_groups.append(self._make_group(indices, sort_values=sort_values))
            for index in indices:
                self.group_id_by_record[index] = gid
        self.group_sorts = {sort: array('I', sorted(range(len(self.market_groups)),
                            key=lambda gid: self._group_order(self.market_groups[gid], sort, sort_values=sort_values)))
                            for sort in SORTS}
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

    def _make_group(self, indices, *, overrides=None, sort_values=None, filters=None):
        values = {i: sort_values[i] if sort_values is not None else
                  (overrides[i]['min_unit_price'], overrides[i]['total_quantity'], overrides[i]['listing_count'],
                   overrides[i]['name'], overrides[i]['market_key']) for i in indices}
        def price_key(i):
            sid = self.records.columns['scan_id'][i].as_py()
            price = values[i][0]
            return price is None, price or 0, -self.scans[sid]['timestamp'], -sid
        winner = min(indices, key=price_key)
        buyouts = {i: overrides[i]['min_buyout'] if overrides is not None else
                   self.records.columns['min_buyout'][i].as_py() for i in indices}
        buyout_winner = min(indices, key=lambda i: (buyouts[i] is None, buyouts[i] or 0, price_key(i)))
        stat_count = None
        if self.records.columns['item_id'][winner].as_py() in RAID_BOE_12_1_ITEM_IDS:
            facts = []
            for i in indices:
                sid = self.records.columns['scan_id'][i].as_py()
                source = self.scans[sid]['source_scan_id']
                for fact in self.raid_facts[source, values[i][4]]:
                    if not filters or filters.matches(self.raid_descriptors[fact['descriptor_id']]):
                        facts.append(fact)
            stat_count = self._stat_combination_count(facts)
        return MarketGroup(tuple(indices), winner, buyout_winner,
                           sum(v[2] for v in values.values()), sum(v[1] for v in values.values()), stat_count)

    def _group_order(self, group, sort, *, overrides=None, sort_values=None):
        if sort_values is not None:
            price, _, _, name, key = sort_values[group.winner]
        else:
            row = overrides[group.winner]
            price, name, key = row['min_unit_price'], row['name'], row['market_key']
        if sort.startswith('price_'):
            return price is None, (price or 0) * (-1 if sort == 'price_desc' else 1), key, group.winner
        if sort == 'quantity_desc':
            return -group.total_quantity, key, group.winner
        if sort == 'listings_desc':
            return -group.listing_count, key, group.winner
        return name, key, group.winner

    def _grouped_items(self, ordered, matched, filters, sort, page, page_size):
        eligible = {self.group_id_by_record[i] for i in ordered}
        overrides = None
        if matched is None:
            groups = [self.market_groups[gid] for gid in self.group_sorts[sort] if gid in eligible]
        else:
            overrides = {self.lookup[row['scan_id'], row['market_key']]: row for row in matched}
            buckets = defaultdict(list)
            for i in overrides:
                buckets[self.group_id_by_record[i]].append(i)
            groups = [self._make_group(ids, overrides=overrides, filters=filters) for ids in buckets.values()]
            groups.sort(key=lambda g: self._group_order(g, sort, overrides=overrides))
        total = len(groups)
        public = []
        for group in groups[(page - 1) * page_size:page * page_size]:
            row = overrides[group.winner] if overrides is not None else self.records[group.winner]
            buyout = overrides[group.buyout_winner] if overrides is not None else self.records[group.buyout_winner]
            item = self._public_record(row)
            buyout_scan = self.scans[buyout['scan_id']]
            item['marketGroup'] = dict(marketCount=len(group.indices), listingCount=group.listing_count,
                totalQuantity=group.total_quantity, minBuyout=buyout['min_buyout'],
                buyoutRealm=buyout_scan['realm_name'], buyoutScannedAt=iso(buyout_scan['timestamp']))
            if group.stat_count is not None:
                item['statCombinationCount'] = group.stat_count
            if filters.active:
                item['variantFilters'] = filters.public()
            public.append(item)
        return dict(scanId=None, page=page, pageSize=page_size, total=total,
                    totalPages=(total + page_size - 1) // page_size, items=public, grouped=True)

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
              difficulty='', stats='', socket='', tertiary='', group_markets=False):
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
        if group_markets and scan_id is None and filters.active:
            # A translated/fallback name in one realm must not hide that same
            # item's eligible quotes from other realms.
            eligible = {self.group_id_by_record[i] for i in ordered}
            ordered = [i for gid in eligible for i in self.market_groups[gid].indices]
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
        if group_markets and scan_id is None:
            return self._grouped_items(ordered, matched, filters, sort, page, page_size)
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
            prototype = None
            for sid in self.latest_scans:
                other = self.scans[sid]
                if other['region_id'] != scan['region_id'] or (other['realm_id'] == 0) != (scan['realm_id'] == 0):
                    continue
                history = self.history_index.get((other['region_id'], other['realm_id'], market_key), ())
                if history:
                    prototype = self.records[history[-1]]
                    break
            if prototype is None:
                raise KeyError('Market item not found in selected scan')
            return scan, dict(prototype, scan_id=scan['scan_id'], listing_count=0, total_quantity=0,
                              variant_count=0, min_unit_price=None, min_buyout=None)
        return scan, self.records[index]

    def listings(self, *, market_key, scan_id=None, page=1, page_size=50,
                 difficulty='', stats='', socket='', tertiary='', public_only=False, all_markets=False):
        if page < 1 or not 1 <= page_size <= 100:
            raise ValueError('Invalid pagination')
        scan, summary = self.require_market(scan_id, market_key)
        filters = VariantFilters(difficulty, stats, socket, tertiary)
        if filters.active and summary['item_id'] not in RAID_BOE_12_1_ITEM_IDS:
            raise ValueError('Equipment filters require a raid item')
        if not all_markets or summary['market_scope'] != 'realm':
            if (scan['scan_id'], market_key) not in self.lookup:
                return {**self._public_record(summary), 'displayColumns': self._listing_columns([]),
                        'page': page, 'pageSize': page_size, 'total': 0, 'totalPages': 0, 'items': []}
            return super().listings(market_key=market_key, scan_id=scan_id, page=page, page_size=page_size,
                difficulty=difficulty, stats=stats, socket=socket, tertiary=tertiary, public_only=public_only)
        if page < 1 or not 1 <= page_size <= 100:
            raise ValueError('Invalid pagination')
        chosen = []
        total = quantity = 0
        descriptors = set()
        observations = []
        for sid in self.latest_scans:
            observed = self.scans[sid]
            if observed['region_id'] != scan['region_id'] or not observed['realm_id']:
                continue
            index = self.lookup.get((sid, market_key))
            if index is None or self.records.columns['market_scope'][index].as_py() != 'realm':
                continue
            row = self.records[index]
            if filters.active:
                facts = [f for f in self.raid_facts[observed['source_scan_id'], market_key]
                         if filters.matches(self.raid_descriptors[f['descriptor_id']])]
                count = sum(f['listing_count'] for f in facts)
                units = sum(f['total_quantity'] for f in facts)
                descriptors.update(f['descriptor_id'] for f in facts)
            else:
                count, units = row['listing_count'], row['total_quantity']
            if not count:
                continue
            chosen.append(observed['source_scan_id'])
            total += count; quantity += units
            observations.append(dict(scanId=sid, realm=observed['realm_name'], realmID=observed['realm_id'],
                                     scannedAt=iso(observed['timestamp'])))
        items = []
        if chosen:
            condition = ' AND descriptor_id IN (' + ','.join('?' for _ in descriptors) + ')' if filters.active else ''
            selected = '''(SELECT * FROM listings WHERE scan_id IN (''' + ','.join('?' for _ in chosen) + ''')
                AND descriptor_id IN (SELECT descriptor_id FROM descriptors WHERE market_key=?)''' + condition + '''
                ORDER BY unit_price ASC NULLS LAST, scan_id, source_index LIMIT ? OFFSET ?)'''
            query = (PUBLIC_LISTINGS_SQL if public_only else RESTORE_SQL).replace('FROM listings f', 'FROM '+selected+' f')
            rows = rows_as_dicts(self.connection().execute(query+' ORDER BY f.unit_price ASC NULLS LAST, f.scan_id, f.source_index',
                [*chosen, market_key, *descriptors, page_size, (page-1)*page_size]))
            for row in rows:
                public = self._public_listing(row, public_only=public_only)
                observed = self.scans[row['scan_id']*2+1]
                public.update(scanId=observed['scan_id'], realm=observed['realm_name'], realmID=observed['realm_id'],
                              scannedAt=iso(observed['timestamp']))
                items.append(public)
        return {**self._public_record(summary), 'scanId': None, 'realm': None, 'realmID': None,
                'allMarkets': True, 'marketCount': len(chosen), 'observations': observations,
                'listingCount': total, 'totalQuantity': quantity, 'displayColumns': self._listing_columns(items),
                'page': page, 'pageSize': page_size, 'total': total,
                'totalPages': (total+page_size-1)//page_size, 'items': items}

    def history(self, *, market_key, scan_id=None, difficulty='', stats='', socket='', tertiary=''):
        scan, current = self.require_market(scan_id, market_key)
        filters = VariantFilters(difficulty, stats, socket, tertiary)
        if filters.active and current['item_id'] not in RAID_BOE_12_1_ITEM_IDS:
            raise ValueError('Equipment history filters require raid equipment')
        indices = self.history_index[scan['region_id'], scan['realm_id'], market_key]
        if not indices:
            return {'marketKey': market_key, 'realmID': scan['realm_id'], 'regionID': scan['region_id'], 'points': []}
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

    def public_history(self, *, market_key, scan_id=None, start=None, end=None, max_points=60,
                       gap_seconds=0, difficulty='', stats='', socket='', tertiary='',
                       all_markets=False, realm_page=1, days=0, history_realm_id=None):
        """Choose at most 60 observations BEFORE decoding summaries or filtering facts.

        Reserve the latest observation of each realm first, then fill the remaining
        budget with the newest observations. More than 60 realms use realm pages.
        """
        import heapq
        if start is not None and end is not None and start > end:
            raise ValueError('History start must not exceed end')
        if max_points < 1 or realm_page < 1 or days not in (0, 7, 30, 90) or gap_seconds < 0:
            raise ValueError('Invalid history bounds')
        limit = min(max_points, 60)
        scan, row = self.require_market(scan_id, market_key)
        filters = VariantFilters(difficulty, stats, socket, tertiary)
        if filters.active and row['item_id'] not in RAID_BOE_12_1_ITEM_IDS:
            raise ValueError('Equipment history filters require raid equipment')
        merged = all_markets and row['market_scope'] == 'realm'
        related = sorted([self.scans[sid] for sid in self.latest_scans
                          if self.scans[sid]['region_id'] == scan['region_id']
                          and self.scans[sid]['realm_id'] > 0], key=lambda s: s['realm_id']) if merged else [scan]
        if history_realm_id is not None:
            if row['market_scope'] != 'realm':
                raise ValueError('Regional commodity history cannot be split by realm')
            related = [self.scans[sid] for sid in self.latest_scans if self.scans[sid]['region_id']==scan['region_id']
                       and self.scans[sid]['realm_id']==history_realm_id and history_realm_id>0]
            if not related:
                raise ValueError('Unknown history realm in this region')
            if not all_markets:
                related = [{**related[0], 'timestamp': min(related[0]['timestamp'],scan['timestamp'])}]
            merged = False
        cutoff = min(end, max(s['timestamp'] for s in related)) if end is not None else max(s['timestamp'] for s in related)
        if days:
            start = max(start or 0, cutoff-days*86400)
        scope_realm = related[0] if not merged else None
        realm_total = len(related)
        realm_pages = max(1, (realm_total+limit-1)//limit)
        related = related[(realm_page-1)*limit:realm_page*limit]
        windows = []
        missing = []
        for observed in related:
            identity = (scan['region_id'], observed['realm_id'])
            timeline = self.scan_timelines[identity]
            indices = self.history_index.get((*identity, market_key), ())
            first = self.scans[self.records.columns['scan_id'][indices[0]].as_py()]['timestamp'] if indices else 0
            lo = bisect_left(timeline, (max(start or 0, first), -1))
            hi = bisect_right(timeline, (cutoff, sys.maxsize))
            if not indices:
                lo = max(lo, hi-1)
            if lo >= hi:
                missing.append(observed['realm_name'])
                continue
            windows.append((observed, timeline, lo, hi))
        selected = []
        heap = []
        for index, (_, timeline, lo, hi) in enumerate(windows):
            selected.append((index, hi-1))
            if hi-2 >= lo:
                timestamp, sid = timeline[hi-2]
                heapq.heappush(heap, (-timestamp, -sid, index, hi-2))
        while heap and len(selected) < limit:
            _, _, index, position = heapq.heappop(heap)
            selected.append((index, position))
            _, timeline, lo, _ = windows[index]
            if position-1 >= lo:
                timestamp, sid = timeline[position-1]
                heapq.heappush(heap, (-timestamp, -sid, index, position-1))
        points = []
        for index, position in selected:
            observed, timeline, _, _ = windows[index]
            timestamp, sid = timeline[position]
            record_index = self.lookup.get((sid, market_key))
            record = self.records[record_index] if record_index is not None else None
            if record and filters.active:
                facts = [f for f in self.raid_facts[self.scans[sid]['source_scan_id'], market_key]
                         if filters.matches(self.raid_descriptors[f['descriptor_id']])]
                record.update(listing_count=sum(f['listing_count'] for f in facts),
                              total_quantity=sum(f['total_quantity'] for f in facts), variant_count=len(facts),
                              min_unit_price=min((f['min_unit_price'] for f in facts if f['min_unit_price'] is not None), default=None),
                              min_buyout=min((f['min_buyout'] for f in facts if f['min_buyout'] is not None), default=None))
            present = bool(record and record['listing_count'])
            points.append(dict(scanId=sid, scannedAt=iso(timestamp), scannedAtUnix=timestamp,
                sourceDate=iso(timestamp)[:10], realm=observed['realm_name'], realmID=observed['realm_id'],
                region=scan['region_name'], regionID=scan['region_id'], marketScope=row['market_scope'],
                state='observed' if present and record['min_unit_price'] is not None else 'no_price' if present else 'absent',
                minUnitPrice=record['min_unit_price'] if present else None, minBuyout=record['min_buyout'] if present else None,
                totalQuantity=record['total_quantity'] if present else 0, listingCount=record['listing_count'] if present else 0,
                variantCount=record['variant_count'] if present else 0))
        points.sort(key=lambda p: (p['realmID'], p['scannedAtUnix'], p['scanId']))
        previous_by_realm = {}
        for point in points:
            previous = previous_by_realm.get(point['realmID'])
            point['breakBefore'] = bool(previous and (previous['minUnitPrice'] is None or point['minUnitPrice'] is None
                or (gap_seconds and point['scannedAtUnix']-previous['scannedAtUnix'] > gap_seconds)))
            previous_by_realm[point['realmID']] = point
        changes = {}
        own_points = [p for p in points if scope_realm and p['realmID'] == scope_realm['realm_id']]
        for metric in ('minUnitPrice', 'minBuyout', 'listingCount', 'totalQuantity'):
            changes[metric] = None
            if len(own_points) >= 2:
                previous, current = own_points[-2][metric], own_points[-1][metric]
                if previous is not None and current is not None:
                    changes[metric] = dict(previous=previous, current=current, absolute=current-previous,
                                          percent=(current-previous)/previous*100 if previous else None)
        points.sort(key=lambda p: (p['scannedAtUnix'], p['scanId']))
        total = sum(hi-lo for _, _, lo, hi in windows)
        return {**self._public_record(row), 'realmID': scope_realm['realm_id'] if scope_realm else None,
                'realm': scope_realm['realm_name'] if scope_realm else None,
                'points': points, 'pointCount': total, 'change': changes,
                'variantFilters': filters.public(), 'historyScope': dict(allMarkets=merged, maxPoints=limit,
                    returnedPoints=len(points), sampled=total>len(points), realmTotal=realm_total,
                    realmPage=realm_page, realmPages=realm_pages, missingRealms=missing, asOf=iso(cutoff), selectedRealmID=history_realm_id)}

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
