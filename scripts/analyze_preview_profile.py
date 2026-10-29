# SPDX-License-Identifier: GPL-3.0-only
"""Aggregate external GUI time series; never edits raw evidence or application data."""
import argparse
import json
from pathlib import Path
import statistics


def slope(points):
    if len(points) < 2: return None
    xbar = statistics.mean(x for x, _ in points)
    ybar = statistics.mean(y for _, y in points)
    denominator = sum((x-xbar)**2 for x, _ in points)
    return sum((x-xbar)*(y-ybar) for x, y in points)/denominator if denominator else None


def analyze(directory, warmup_seconds=300):
    summary = json.loads((directory/'summary.json').read_text('utf-8'))
    rows = [json.loads(line) for line in (directory/'series.jsonl').read_text('utf-8').splitlines()]
    samples = []
    for row in rows:
        main = next(p for p in row['closed'] if p['pid'] == summary['pid'])
        samples.append(dict(seconds=row['elapsed_s'], format=row['format'], load_ms=row['load_ms'],
                            working_mib=main['working_bytes']/2**20, private_mib=main['private_bytes']/2**20,
                            handles=main['handles'], threads=main['threads'],
                            processes=len(row['closed']), qt=row['objects']['qt_cpp_live'],
                            python_objects=row['objects']['tracked_objects'], lifecycle=row['lifecycle']))
    warm = [r for r in samples if r['seconds'] >= warmup_seconds]
    def describe(values):
        if not values: return None
        return dict(min=min(values), max=max(values), median=statistics.median(values))
    formats = {}
    for kind in sorted({r['format'] for r in samples}):
        selected = [r for r in samples if r['format'] == kind]
        formats[kind] = dict(successes=len(selected), load_ms=describe([r['load_ms'] for r in selected if r['load_ms'] is not None]))
    buckets = []
    for index in sorted({int(r['seconds']//300) for r in samples}):
        selected = [r for r in samples if int(r['seconds']//300) == index]
        buckets.append(dict(start_minute=index*5, count=len(selected),
                            private_mib=describe([r['private_mib'] for r in selected]),
                            working_mib=describe([r['working_mib'] for r in selected]),
                            handles=describe([r['handles'] for r in selected]),
                            threads=describe([r['threads'] for r in selected])))
    return dict(passed=summary['passed'], duration_seconds=summary.get('elapsed_s'), cycles=len(samples),
                warmup_seconds=warmup_seconds, formats=formats, first=samples[0], last=samples[-1],
                warm_private_mib=describe([r['private_mib'] for r in warm]),
                private_mib_per_minute=slope([(r['seconds']/60,r['private_mib']) for r in warm]),
                last_15min_mib_per_minute=slope([(r['seconds']/60,r['private_mib']) for r in warm if r['seconds']>=samples[-1]['seconds']-900]),
                warm_first_20_median=statistics.median(r['private_mib'] for r in warm[:20]) if warm else None,
                warm_last_20_median=statistics.median(r['private_mib'] for r in warm[-20:]) if warm else None,
                max_closed_processes=max(r['processes'] for r in samples),
                closed_qt_range=describe([r['qt'] for r in samples]),
                buckets=buckets, files_and_business_unchanged=summary.get('files_and_business_unchanged'))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('directory',type=Path)
    parser.add_argument('--warmup-seconds',type=float,default=300)
    args = parser.parse_args()
    print(json.dumps(analyze(args.directory,args.warmup_seconds),ensure_ascii=True,indent=2))
