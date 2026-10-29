"""Bound chart payloads while retaining extrema and explicit discontinuities."""
import math


def sample_points(points, max_points=400, gap_seconds=0):
    if not 8 <= max_points <= 2000 or gap_seconds < 0:
        raise ValueError('Invalid history resolution or gap interval')
    points = [dict(p) for p in points]
    for index, point in enumerate(points):
        previous = points[index - 1] if index else None
        point['breakBefore'] = bool(previous and (
            previous['minUnitPrice'] is None or point['minUnitPrice'] is None
            or (gap_seconds and point['scannedAtUnix'] - previous['scannedAtUnix'] > gap_seconds)))
    if len(points) <= max_points:
        return points
    selected = {0, len(points) - 1}
    bucket_size = math.ceil(len(points) / max(1, (max_points - 2) // 4))
    for start in range(0, len(points), bucket_size):
        end = min(start + bucket_size, len(points))
        selected.update((start, end - 1))
        priced = [i for i in range(start, end) if points[i]['minUnitPrice'] is not None]
        if priced:
            selected.add(min(priced, key=lambda i: points[i]['minUnitPrice']))
            selected.add(max(priced, key=lambda i: points[i]['minUnitPrice']))
    result = []
    previous = -1
    for index in sorted(selected):
        point = points[index]
        point['breakBefore'] = any(p['breakBefore'] for p in points[previous + 1:index + 1])
        result.append(point)
        previous = index
    return result
