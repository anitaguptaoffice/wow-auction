"""Shared retention settings; compatible with the Python 3.6 host."""
import time


DEFAULTS = dict(retention_days=14, min_snapshots_per_day=1, max_snapshots=200, timezone='Asia/Shanghai',
                rollback_hours=24)


def validate(value):
    if not isinstance(value, dict) or set(value) - set(DEFAULTS):
        raise ValueError('Unknown retention policy fields')
    policy = dict(DEFAULTS, **value)
    for key, maximum in (('retention_days', 14), ('min_snapshots_per_day', 1000),
                         ('max_snapshots', 1000000), ('rollback_hours', 336)):
        if type(policy[key]) is not int or not 1 <= policy[key] <= maximum:
            raise ValueError('%s must be an integer between 1 and %d' % (key, maximum))
    if not isinstance(policy['timezone'], str) or not policy['timezone']:
        raise ValueError('timezone must be an IANA timezone name')
    return policy


def require_fresh(captured_at, policy, now=None):
    policy = validate(policy)
    cutoff = int(time.time() if now is None else now) - policy['retention_days'] * 86400
    if captured_at < cutoff:
        raise ValueError('Snapshot is older than the configured retention window')
