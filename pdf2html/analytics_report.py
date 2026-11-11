"""Summarize exported analytics locally; never connects to AWS or exposes an API."""
import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import re
from statistics import mean, median

from usage_analytics import EVENT_FIELDS, MARKER, USAGE_FIELDS


def _events(value):
    if isinstance(value, list):
        for item in value:
            yield from _events(item)
    elif isinstance(value, dict):
        if value.get('analytics') == MARKER:
            yield value
        elif isinstance(value.get('message'), str):
            try:
                yield from _events(json.loads(value['message']))
            except ValueError:
                pass
        elif isinstance(value.get('events'), list):
            yield from _events(value['events'])


def read_events(path):
    text = Path(path).read_text(encoding='utf-8-sig')
    try:
        yield from _events(json.loads(text))
    except ValueError:
        for line in text.splitlines():
            try:
                yield from _events(json.loads(line))
            except ValueError:
                pass


def summarize(events, details=False):
    seen = set()
    totals = Counter()
    days = defaultdict(Counter)
    usage = Counter()
    timings = defaultdict(list)
    errors = Counter()
    groups = {'browsers': defaultdict(Counter), 'delivered_documents': defaultdict(Counter)}
    duplicates = invalid = 0
    def numeric(value):
        return type(value) in (int, float) and math.isfinite(value) and 0 <= value <= 10**12
    for event in events:
        try:
            if (event.get('analytics') != MARKER or type(event.get('schema_version')) is not int
                    or event['schema_version'] != 1 or event.get('event') not in EVENT_FIELDS
                    or not re.fullmatch(r'[0-9a-f]{32}', event.get('event_id', ''))):
                raise ValueError('Invalid schema')
            timestamp = datetime.fromisoformat(event['timestamp'].replace('Z', '+00:00'))
            if timestamp.tzinfo is None:
                raise ValueError('Timestamp needs a time zone')
            day = timestamp.astimezone(timezone.utc).date().isoformat()
        except (ValueError, KeyError, TypeError, AttributeError):
            invalid += 1
            continue
        if event['event_id'] in seen:
            duplicates += 1
            continue
        seen.add(event['event_id'])
        name = event['event']
        for group, field, prefix in (('browsers', 'browser_id', 'b1_'), ('delivered_documents', 'document_id', 'd1_')):
            identity = event.get(field)
            if isinstance(identity, str) and re.fullmatch(prefix + r'[0-9a-f]{64}', identity):
                bucket = groups[group][identity]
                bucket[name] += 1
                if name == 'html_response' and event.get('http_status') in (200, 206) and isinstance(event.get('html_kind'), str) and event['html_kind'] in {'selected_preview', 'legacy_preview'}:
                    bucket['preview_deliveries'] += 1
                if name in {'job_completed', 'job_failed'}:
                    for metric in ('bda_pages', 'estimated_reported_ai_cost_usd'):
                        if numeric(event.get(metric)):
                            bucket[metric] += event[metric]
        totals[name] += 1
        days[day][name] += 1
        if name == 'html_response' and event.get('http_status') in (200, 206) and isinstance(event.get('html_kind'), str) and event.get('html_kind') in {'selected_preview', 'legacy_preview'}:
            totals['preview_deliveries'] += 1
            days[day]['preview_deliveries'] += 1
        if name in {'job_completed', 'job_failed'}:
            for field in USAGE_FIELDS | {'estimated_reported_ai_cost_usd'}:
                if numeric(event.get(field)):
                    usage[field] += event[field]
            totals['jobs_with_cost_estimate' if numeric(event.get('estimated_reported_ai_cost_usd')) else 'jobs_without_cost_estimate'] += 1
        if name == 'job_completed':
            for field in ('queue_ms', 'processing_ms', 'elapsed_ms'):
                if numeric(event.get(field)):
                    timings[field].append(event[field])
        if name == 'job_failed':
            from usage_analytics import ENUMS
            category = event.get('error_category')
            if isinstance(category, str) and category in ENUMS['error_category']:
                errors[category] += 1
    durations = {}
    for name, values in timings.items():
        values.sort()
        durations[name] = {'samples': len(values), 'mean': round(mean(values), 2),
                           'median': median(values), 'p95_nearest_rank': values[math.ceil(.95 * len(values))-1]}
    result = {'schema_version': 1, 'daily_timezone': 'UTC', 'events': dict(totals),
            'daily': {day: dict(counts) for day, counts in sorted(days.items())},
            'reported_ai_usage': dict(usage), 'completed_job_timings_ms': durations,
            'failure_categories': dict(errors), 'duplicate_events_ignored': duplicates,
            'invalid_analytics_events_ignored': invalid,
            'identified_browsers': len(groups['browsers']), 'identified_delivered_documents': len(groups['delivered_documents']),
            'limitations': 'Browser IDs are unverified, not people. Documents group ILLiad deliveries, or individual jobs without a delivery number, not unique articles. Pages/costs are captured processing usage, including failures, not audited remediation or billing. Costs exclude unreported usage and infrastructure. Refreshes/prefetches repeat deliveries; interrupted jobs can lack terminal events.'}
    if details:
        result['private_breakdown'] = {name: {identity: dict(values) for identity, values in sorted(items.items())}
                                       for name, items in groups.items()}
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('input', type=Path, help='JSONL or CloudWatch events/message JSON export')
    parser.add_argument('--output', type=Path)
    parser.add_argument('--details', action='store_true', help='Include private pseudonymous per-browser/document breakdowns')
    args = parser.parse_args()
    result = json.dumps(summarize(read_events(args.input), details=args.details), indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open('x', encoding='utf-8') as stream:
            stream.write(result + '\n')
    print(result)


if __name__ == '__main__':
    main()
