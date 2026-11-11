"""Allowlisted operational events with optional pseudonymous correlation."""
from datetime import datetime, timezone
import hashlib
import hmac
import json
import math
import os
import re
import sys
import threading
import time
import uuid


MARKER = 'pdf2html_usage'
OUTPUT_LOCK = threading.Lock()
ENUMS = {
    'channel': {'api', 'form'},
    'workflow': {'full', 'tables', 'local_tables'},
    'cache_state': {'completed', 'queued', 'processing'},
    'error_category': {'timeout', 'storage_error', 'validation_error', 'processing_error'},
    'html_kind': {'selected_preview', 'legacy_preview', 'other_html'},
    'document_scope': {'illiad_delivery', 'job'},
    'cost_coverage': {'partial', 'unavailable'},
}
CONTEXT_FIELDS = {'browser_id', 'document_id', 'document_scope'}
COST_FIELDS = {'estimated_reported_ai_cost_usd', 'cost_coverage'}
USAGE_FIELDS = {
    'bda_documents', 'bda_pages', 'bedrock_calls', 'bedrock_input_tokens', 'bedrock_output_tokens',
    'openai_attempts', 'openai_responses', 'openai_input_tokens', 'openai_output_tokens',
    'openai_images_generated', 'openai_failures', 'bedrock_usage_available', 'openai_usage_available',
}
JOB_FIELDS = {'channel', 'workflow', 'queue_ms', 'processing_ms', 'elapsed_ms'} | USAGE_FIELDS | COST_FIELDS
EVENT_FIELDS = {
    'submission_requested': {'channel'},
    'submission_error': {'channel', 'http_status'},
    'job_queued': {'channel', 'workflow'},
    'job_started': {'channel', 'workflow', 'queue_ms'},
    'job_completed': JOB_FIELDS | {'issues_found', 'issues_remediated'},
    'job_failed': JOB_FIELDS | {'error_category'},
    'job_cache_hit': {'channel', 'workflow', 'cache_state'},
    'html_response': {'html_kind', 'http_status'},
    'zip_response': {'http_status'},
}
for _event in ('job_queued', 'job_started', 'job_completed', 'job_failed', 'job_cache_hit', 'html_response'):
    EVENT_FIELDS[_event] |= CONTEXT_FIELDS


def correlation(client_id=None, transaction=None, job_id=None):
    """Untrusted analytics hints, never authentication or authorization."""
    try:
        if not enabled():
            return {}
        secret = os.getenv('ANALYTICS_ID_KEY', '')
        if not re.fullmatch(r'[0-9a-fA-F]{64}', secret):
            return {}
        def digest(domain, value):
            return hmac.new(bytes.fromhex(secret), (domain + '\0' + value).encode('utf-8'), hashlib.sha256).hexdigest()
        result = {}
        if isinstance(client_id, str) and re.fullmatch(r'[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}', client_id.lower()):
            result['browser_id'] = 'b1_' + digest('browser-v1', client_id.lower())
        if isinstance(transaction, str) and re.fullmatch(r'[0-9]{1,20}', transaction):
            result.update(document_id='d1_' + digest('illiad-delivery-v1', transaction), document_scope='illiad_delivery')
        elif isinstance(job_id, str) and re.fullmatch(r'(?:[0-9a-f]{12}|[0-9a-f]{32})', job_id):
            result.update(document_id='d1_' + digest('job-v1', job_id), document_scope='job')
        return result
    except Exception:
        return {}


def enabled():
    return os.getenv('ACCESSIBLE_HTML_ANALYTICS_ENABLED', 'true').strip().lower() in {'1', 'true', 'yes', 'on'}


def record_event(event, **fields):
    """Never serialize arbitrary request, model, exception, or document values."""
    try:
        if not enabled() or event not in EVENT_FIELDS:
            return False
        payload = {'analytics': MARKER, 'schema_version': 1, 'event': event,
                   'event_id': uuid.uuid4().hex, 'timestamp': datetime.now(timezone.utc).isoformat()}
        for name in EVENT_FIELDS[event]:
            value = fields.get(name)
            if name in ENUMS:
                if isinstance(value, str) and value in ENUMS[name]:
                    payload[name] = value
            elif name in {'browser_id', 'document_id'}:
                prefix = 'b1_' if name == 'browser_id' else 'd1_'
                if isinstance(value, str) and re.fullmatch(prefix + r'[0-9a-f]{64}', value):
                    payload[name] = value
            elif type(value) in (int, float) and math.isfinite(value) and 0 <= value <= 10**12:
                payload[name] = value
        line = json.dumps(payload, separators=(',', ':'), allow_nan=False)
        with OUTPUT_LOCK:
            sys.stdout.write(line + '\n')
            sys.stdout.flush()
        return True
    except Exception:
        # Analytics must not break conversion/delivery, or log a sensitive error body.
        return False


def provider_metrics(aws=None, openai=None):
    """Extract totals only; raw reports contain identifiers and must not be logged."""
    result = {'bedrock_usage_available': int(isinstance(aws, dict)),
              'openai_usage_available': int(isinstance(openai, dict))}
    def number(data, key):
        value = data.get(key, 0) if isinstance(data, dict) else 0
        return value if type(value) is int and 0 <= value <= 10**12 else 0
    if isinstance(aws, dict):
        bda, bedrock = aws.get('bda_usage', {}), aws.get('bedrock_usage', {})
        for name, data, key in (
            ('bda_documents', bda, 'total_documents_processed'), ('bda_pages', bda, 'total_pages_processed'),
            ('bedrock_calls', bedrock, 'total_calls'), ('bedrock_input_tokens', bedrock, 'total_input_tokens'),
            ('bedrock_output_tokens', bedrock, 'total_output_tokens')):
            result[name] = number(data, key)
    if isinstance(openai, dict):
        for name, key in (('openai_attempts', 'calls'), ('openai_images_generated', 'generated'), ('openai_failures', 'failed')):
            result[name] = number(openai, key)
        entries = openai.get('usage', [])
        entries = [item for item in entries if isinstance(item, dict)] if isinstance(entries, list) else []
        result.update(openai_responses=len(entries),
                      openai_input_tokens=sum(number(item, 'input_tokens') for item in entries),
                      openai_output_tokens=sum(number(item, 'output_tokens') for item in entries))
    result.update(cost_metrics(aws, openai))
    return result


def cost_metrics(aws, openai):
    """Estimate captured usage only, using operator-supplied contract rates.

    Unknown rates are omitted, never silently treated as free. Even known rates
    cannot account for missing usage, hosting, storage, tax or discounts.
    """
    result = {'cost_coverage': 'unavailable'}
    try:
        config = json.loads(os.getenv('ANALYTICS_RATES_JSON', '{}'))
        if not isinstance(config, dict):
            return result
        total, priced = 0.0, False
        def charge(count, rate, divisor=1):
            if (type(count) not in (int, float) or not math.isfinite(count) or count < 0 or
                    type(rate) not in (int, float) or not math.isfinite(rate) or rate < 0):
                return None
            return count * rate / divisor
        if isinstance(aws, dict):
            cost = charge(aws.get('bda_usage', {}).get('total_pages_processed'), config.get('bda_per_page'))
            if cost is not None:
                total += cost; priced = True
            for model, usage in aws.get('bedrock_usage', {}).get('calls_by_model', {}).items():
                rates = config.get('bedrock', {}).get(model, {})
                amounts = [charge(usage.get('input_tokens'), rates.get('input_per_million'), 1e6),
                           charge(usage.get('output_tokens'), rates.get('output_per_million'), 1e6)]
                if all(value is not None for value in amounts):
                    total += sum(amounts); priced = True
        if isinstance(openai, dict):
            rates = config.get('openai', {}).get(openai.get('model'), {})
            for usage in openai.get('usage', []):
                inputs = usage.get('input_tokens')
                cached = usage.get('input_tokens_details', {}).get('cached_tokens', 0)
                if type(inputs) is not int or type(cached) is not int or not 0 <= cached <= inputs:
                    continue
                amounts = [charge(inputs-cached, rates.get('input_per_million'), 1e6),
                           charge(usage.get('output_tokens'), rates.get('output_per_million'), 1e6)]
                if cached:
                    amounts.append(charge(cached, rates.get('cached_input_per_million'), 1e6))
                if all(value is not None for value in amounts):
                    total += sum(amounts); priced = True
        if priced and math.isfinite(total):
            result.update(estimated_reported_ai_cost_usd=round(total, 8), cost_coverage='partial')
    except (ValueError, TypeError, AttributeError, OverflowError):
        pass
    return result


class JobTimer:
    def __init__(self, channel, workflow, context=None):
        self.fields = {'channel': channel, 'workflow': workflow}
        self.fields.update({key: value for key, value in (context or {}).items() if key in CONTEXT_FIELDS})
        self.queued = time.monotonic()
        self.started_at = None
        self.terminal = False
        record_event('job_queued', **self.fields)

    def start(self):
        self.started_at = time.monotonic()
        self.fields['queue_ms'] = round(max(0, self.started_at - self.queued) * 1000)
        record_event('job_started', **self.fields)

    def _finish(self, event, **extra):
        if self.terminal:
            return
        self.terminal = True
        now = time.monotonic()
        self.fields.update(elapsed_ms=round(max(0, now - self.queued) * 1000), **extra)
        if self.started_at is not None:
            self.fields['processing_ms'] = round(max(0, now - self.started_at) * 1000)
        record_event(event, **self.fields)

    def complete(self, result):
        counts = {}
        try:
            audit = result.get('audit_result') or {}
            remediation = result.get('remediation_result') or {}
            counts.update(issues_found=len(audit.get('issues', [])), issues_remediated=remediation.get('issues_remediated', 0))
        except (AttributeError, TypeError):
            pass
        self._finish('job_completed', **counts)

    def fail(self, error):
        category = ('timeout' if isinstance(error, TimeoutError) else
                    'storage_error' if isinstance(error, OSError) else
                    'validation_error' if isinstance(error, ValueError) else 'processing_error')
        self._finish('job_failed', error_category=category)
