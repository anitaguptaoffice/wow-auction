"""Versioned, bounded JSONL+gzip snapshots; re-readable rows avoid retaining raw auctions."""
from collections.abc import Sequence
from datetime import datetime, timedelta, timezone
import gzip
import json
from pathlib import Path

MAX_LINE = 64 * 1024
MAX_BYTES = 1024 * 1024 * 1024
MAX_RECORDS = 1_000_000


def integer(value, name, minimum=0, maximum=2**63-1):
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError('Invalid integer: '+name)
    return value


def lines(path):
    total = 0
    with gzip.open(path, 'rb') as source:
        while True:
            line = source.readline(MAX_LINE+1)
            if not line:
                return
            total += len(line)
            if len(line)>MAX_LINE or total>MAX_BYTES:
                raise ValueError('Snapshot exceeds decompressed size or line limit')
            def reject_constant(value):
                raise ValueError('Non-finite JSON value')
            value = json.loads(line.decode('utf-8'), parse_constant=reject_constant)
            if not isinstance(value, dict):
                raise ValueError('Every snapshot line must be a JSON object')
            yield value


def header(path):
    value = next(lines(path), None)
    if not value or value.get('type')!='snapshot' or type(value.get('schema_version')) is not int or value.get('schema_version')!=1 or value.get('complete') is not True:
        raise ValueError('Expected a complete version 1 snapshot header')
    for key in ('region_id','realm_id','captured_at'):
        integer(value.get(key), key, 1, 2**31-1 if key!='captured_at' else 2**63-1)
    integer(value.get('record_count'), 'record_count', 0, MAX_RECORDS)
    if not isinstance(value.get('realm_name'), str) or not 1<=len(value['realm_name'])<=100:
        raise ValueError('realm_name is required')
    if value.get('source_date'):
        datetime.strptime(value['source_date'], '%Y-%m-%d')
    return value


class SnapshotRows(Sequence):
    def __init__(self, path, count):
        self.path, self.count = Path(path), count

    def __len__(self):
        return self.count

    def __getitem__(self, index):
        if not isinstance(index, int):
            raise TypeError('Only integer row indices are supported')
        if index < 0:
            index += self.count
        for current, value in enumerate(self):
            if current==index:
                return value
        raise IndexError(index)

    def __iter__(self):
        source = lines(self.path)
        next(source)
        count = 0
        for item in source:
            count += 1
            if count>self.count or item.get('type')!='auction':
                raise ValueError('Unexpected record or row count mismatch')
            for key in ('itemID','quantity'):
                integer(item.get(key), key, 1, 2**32-1)
            for key in ('minBid','buyoutAmount','bidAmount'):
                integer(item.get(key), key)
            integer(item.get('timeLeftBand'), 'timeLeftBand', 1, 4)
            for key in ('texture','minIncrement','level','qualityID','saleStatus','battlePetCreatureID','battlePetDisplayID'):
                if item.get(key) is not None:
                    integer(item[key], key, 0, 2**31-1)
            for key in ('usable','highBidder','hasAllInfo'):
                if item.get(key) is not None and type(item[key]) is not bool:
                    raise ValueError('Invalid boolean: '+key)
            for key in ('name','itemLink','owner','ownerFullName','bidderFullName','levelType'):
                if item.get(key) is not None and (not isinstance(item[key],str) or len(item[key])>4096):
                    raise ValueError('Invalid string: '+key)
            if not item.get('name') or not item.get('itemLink') or item.get('hasAllInfo') is not True:
                raise ValueError('Incomplete auction record')
            if item.get('marketScope') not in ('region','realm'):
                raise ValueError('Every auction needs an explicit region/realm marketScope')
            yield item
        if count!=self.count:
            raise ValueError('Snapshot record_count does not match its rows')


def decode(path):
    meta = header(path)
    count = meta['record_count']
    date = meta.get('source_date') or datetime.fromtimestamp(meta['captured_at'], timezone(timedelta(hours=8))).strftime('%Y-%m-%d')
    scan = dict(timestamp=meta['captured_at'], itemCount=count, recordCount=count, linkedItemCount=count,
                missingCoreCount=0, incompleteInfoCount=0, apiErrorCount=0,
                realmID=meta['realm_id'], realmName=meta['realm_name'],
                normalizedRealmName=meta.get('normalized_realm_name'), regionID=meta['region_id'],
                regionName=meta.get('region_name'), items=SnapshotRows(path,count))
    return {'auctions': {date: {'scans': [scan]}}}
