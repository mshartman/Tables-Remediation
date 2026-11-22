"""Private CloudWatch usage dashboard; no service changes or model calls."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import subprocess
import time
from urllib.parse import quote


DEFAULT_NAME = 'PDFtoHTML-Usage'
DEFAULT_REGION = 'us-east-1'
ROOT = Path(__file__).resolve().parent
FILTER = 'filter analytics = "pdf2html_usage" and schema_version = 1'
TERMINAL = 'event in ["job_completed", "job_failed"]'
DELIVERY = ('event = "html_response" and html_kind in ["selected_preview", "legacy_preview"] '
            'and http_status in [200, 206]')


def validate_log_group(value):
    if not isinstance(value, str) or not re.fullmatch(
            r'/aws/apprunner/[A-Za-z0-9_-]+/[a-f0-9]{32}/application', value):
        raise ValueError('Select an App Runner application log group, not a service/deployment group.')
    return value


def validate_region(value):
    if not re.fullmatch(r'[a-z]{2}(?:-[a-z]+)+-[0-9]+', value):
        raise ValueError('Invalid AWS region.')
    return value


def dashboard_url(name=DEFAULT_NAME, region=DEFAULT_REGION):
    return ('https://console.aws.amazon.com/cloudwatch/home?region=' + quote(region, safe='')
            + '#dashboards/dashboard/' + quote(name, safe=''))


def queries():
    """All grouped fields are analytics allowlist fields, never raw messages."""
    event_flags = (
        'fields if(event = "job_completed", 1, 0) as completed, '
        'if(event = "job_failed", 1, 0) as failed, '
        'if(event = "job_cache_hit", 1, 0) as reused, '
        f'if({DELIVERY}, 1, 0) as delivered, '
        f'if({TERMINAL}, bda_pages, 0) as processed_pages'
    )
    return {
        'overview': event_flags + '\n| stats sum(completed) as completed_conversions, '
            'sum(failed) as failed_jobs, sum(processed_pages) as recorded_pages, '
            'sum(delivered) as html_deliveries, sum(reused) as cache_reuses',
        'cost_total': f'filter {TERMINAL}\n'
            '| stats count(*) as terminal_jobs, count(estimated_reported_ai_cost_usd) as priced_samples, '
            'sum(estimated_reported_ai_cost_usd) as estimate_sum\n'
            '| fields terminal_jobs, coalesce(priced_samples, 0) as priced_jobs, '
            'terminal_jobs - coalesce(priced_samples, 0) as unpriced_jobs, '
            'if(coalesce(priced_samples, 0) > 0, coalesce(estimate_sum, 0), "Unavailable") as partial_ai_estimate_usd',
        'coverage': 'fields '
            f'if({TERMINAL}, 1, 0) as finished, '
            f'if({TERMINAL} and ispresent(bda_pages), 1, 0) as page_usage, '
            f'if({TERMINAL} and ispresent(estimated_reported_ai_cost_usd), 1, 0) as priced, '
            f'if({DELIVERY}, 1, 0) as delivered, '
            f'if({DELIVERY} and ispresent(browser_id), 1, 0) as browser_tagged, '
            f'if({DELIVERY} and ispresent(document_id), 1, 0) as document_tagged\n'
            '| stats count(*) as analytics_events, max(@timestamp) as latest_event, '
            'sum(finished) as terminal_jobs, sum(page_usage) as jobs_with_page_data, '
            'sum(priced) as jobs_with_cost_data, sum(delivered) as html_deliveries, '
            'sum(browser_tagged) as deliveries_with_browser_id, '
            'sum(document_tagged) as deliveries_with_document_id',
        'daily_conversions': 'filter event in ["job_completed", "job_failed"]\n'
            '| fields if(event = "job_completed", 1, 0) as completed_flag, '
            'if(event = "job_failed", 1, 0) as failed_flag\n'
            '| stats sum(completed_flag) as completed, sum(failed_flag) as failed by bin(1d)',
        'daily_pages': f'filter {TERMINAL} and ispresent(bda_pages)\n'
            '| stats sum(bda_pages) as recorded_pages by bin(1d)',
        'per_pdf': 'filter ispresent(document_id) and '
            'event in ["job_completed", "job_failed", "job_cache_hit", "html_response"]\n| '
            + event_flags + '\n'
            '| stats sum(completed) as conversions, sum(failed) as failures, '
            'sum(processed_pages) as recorded_pages, sum(delivered) as html_deliveries, '
            'sum(reused) as cache_reuses, count_distinct(browser_id) as browsers_seen '
            'by document_id, document_scope\n| sort html_deliveries desc\n| limit 100',
        'cost_per_pdf': f'filter {TERMINAL} and ispresent(document_id)\n'
            '| stats count(*) as terminal_jobs, count(estimated_reported_ai_cost_usd) as priced_samples, '
            'sum(estimated_reported_ai_cost_usd) as estimate_sum by document_id, document_scope\n'
            '| fields document_id, document_scope, terminal_jobs, coalesce(priced_samples, 0) as priced_jobs, '
            'terminal_jobs - coalesce(priced_samples, 0) as unpriced_jobs, '
            'if(coalesce(priced_samples, 0) > 0, coalesce(estimate_sum, 0), "Unavailable") as partial_ai_estimate_usd\n'
            '| sort terminal_jobs desc\n| limit 100',
        'per_browser': f'filter {DELIVERY} and ispresent(browser_id)\n'
            '| stats count(*) as html_deliveries, count_distinct(document_id) as identified_documents '
            'by browser_id\n| sort html_deliveries desc\n| limit 100',
        'performance': 'filter event = "job_completed"\n'
            '| stats count(*) as completed, avg(queue_ms)/60000 as mean_queue_minutes, '
            'avg(processing_ms)/60000 as mean_processing_minutes, '
            'pct(elapsed_ms, 95)/60000 as p95_elapsed_minutes by bin(1d)',
        'failures': 'filter event = "job_failed"\n'
            '| fields coalesce(error_message, "Legacy failure: reason was not captured in analytics") as failure_reason\n'
            '| stats count(*) as recorded_failures by error_category, failure_reason\n| sort recorded_failures desc',
        'failure_details': 'filter event = "job_failed"\n'
            '| fields @timestamp, coalesce(error_message, "Legacy failure: open application logs for details") as failure_reason, '
            'error_category, error_code, error_stage, bda_timeout_seconds, bda_last_status, aws_error_code, '
            'processing_ms / 60000 as processing_minutes, document_id, event_id, @logStream\n'
            '| sort @timestamp desc\n| limit 100',
        'legacy_bda_timeouts': 'filter @message like /bedrock_client/ and @message like /BDA job timed out after [0-9]+s/\n'
            '| parse @message /BDA job timed out after (?<wait_seconds>[0-9]+)s/\n'
            '| fields "BDA polling timed out" as failure_reason\n'
            '| stats latest(@timestamp) as failure_time by bin(1s), @logStream, wait_seconds, failure_reason\n'
            '| sort failure_time desc\n| limit 100',
    }


def build_dashboard(log_group, region=DEFAULT_REGION):
    validate_log_group(log_group)
    validate_region(region)
    widgets = []

    def text(markdown, y, height=4):
        widgets.append({'type': 'text', 'x': 0, 'y': y, 'width': 24, 'height': height,
                        'properties': {'markdown': markdown}})

    def log(key, title, x, y, width=12, height=5, view='table', legacy=False):
        query = f"SOURCE '{log_group}'\n| " + ('' if legacy else FILTER + '\n| ') + queries()[key]
        widgets.append({'type': 'log', 'x': x, 'y': y, 'width': width, 'height': height,
                        'properties': {'region': region, 'title': title, 'query': query, 'view': view}})

    text('# PDF to HTML - private usage dashboard\n'
         'Choose a time range at the top; the default is the last 7 days (including testing activity). Refresh when needed. '
         'Generated by `usage_dashboard.py`; rerunning setup requires `--replace` and preserves a backup.\n\n'
         '**Recorded pages include captured processing on failed jobs, not verified accessible pages.** '
         'HTML deliveries include refreshes/range requests, not unique reads. Private PDF IDs group an '
         'ILLiad delivery (or a job), not necessarily a unique article. Browser IDs are not people. '
         'Daily buckets are UTC. Blank results or unavailable IDs do not prove zero usage.', 0)
    log('overview', 'Selected period - conversions, recorded pages and HTML deliveries', 0, 4, 24, 4)
    log('cost_total', 'Partial AI estimates (USD) - check unpriced jobs', 0, 8, height=5)
    log('coverage', 'Data coverage and latest event - check before interpreting totals', 12, 8, height=5)
    log('daily_conversions', 'Conversions and failures per day (UTC)', 0, 13, height=6, view='timeSeries')
    log('daily_pages', 'Recorded pages processed per day (including captured failed work)', 12, 13, height=6, view='timeSeries')
    log('per_pdf', 'Usage per delivered PDF - private IDs (top 100)', 0, 19, 24, 8)
    log('cost_per_pdf', 'Partial AI estimates per PDF - unavailable is not zero', 0, 27, height=7)
    log('per_browser', 'Successful HTML deliveries per identified browser - not patrons', 12, 27, height=7)
    log('performance', 'Completed-job timing (minutes; excludes upload and browser loading)', 0, 34, height=6)
    log('failures', 'Recorded job failures by category', 12, 34, height=6)
    log('failure_details', 'Failed conversions - reason and diagnostic reference', 0, 40, 24, 8)
    log('legacy_bda_timeouts', 'Legacy BDA timeout diagnostics - seconds waited (duplicate log lines consolidated)', 0, 48, 24, 5, legacy=True)
    text('## Costs: estimates versus the AWS bill\n'
         '**Per-PDF AI values are partial, only for jobs with configured rates and recorded usage.** '
         'Unpriced jobs are not free. Existing events are not backfilled when rates are added. '
         'These values exclude hosting, storage, logging, missing retries, tax and discounts.\n\n'
         'The AWS Billing widgets below show cumulative month-to-date **account-wide estimates**, '
         'not the selected period total, per-PDF costs or a final invoice; AWS does not include the OpenAI bill. '
         'Missing billing data is unavailable, not $0. If blank, enable **Receive CloudWatch Billing Alerts** '
         'under [Billing preferences](https://console.aws.amazon.com/billing/home#/preferences). '
         'AWS says that collection cannot be disabled after enabling; no alarms are created by this dashboard. '
         'Allow about 15 minutes and refresh. [AWS billing](https://console.aws.amazon.com/costmanagement/home) '
         '| [OpenAI usage](https://platform.openai.com/usage). '
         'Dashboard loads/refreshes run log queries and can incur CloudWatch charges; avoid frequent auto-refresh.', 53, 6)
    widgets.append({'type': 'metric', 'x': 0, 'y': 59, 'width': 8, 'height': 6,
                    'properties': {'region': 'us-east-1',
                                   'title': 'AWS account - latest month-to-date estimate (USD)',
                                   'metrics': [['AWS/Billing', 'EstimatedCharges', 'Currency', 'USD']],
                                   'view': 'singleValue', 'stat': 'Maximum', 'period': 21600,
                                   'liveData': False}})
    widgets.append({'type': 'metric', 'x': 8, 'y': 59, 'width': 16, 'height': 6,
                    'properties': {'region': 'us-east-1',
                                   'title': 'AWS services - cumulative monthly estimates, not daily spend',
                                   'metrics': [[{'expression': "SEARCH('{AWS/Billing,Currency,ServiceName} MetricName=\"EstimatedCharges\" Currency=\"USD\"', 'Maximum', 21600)",
                                                 'id': 'billing'}]],
                                   'view': 'timeSeries', 'stat': 'Maximum', 'period': 21600,
                                   'liveData': False}})
    return {'start': '-P7D', 'periodOverride': 'inherit', 'widgets': widgets}


class AwsError(RuntimeError):
    pass


def aws(*args, region=DEFAULT_REGION):
    command = ['aws', *args, '--region', region, '--output', 'json', '--no-cli-pager',
               '--cli-connect-timeout', '5', '--cli-read-timeout', '30']
    try:
        result = subprocess.run(command, capture_output=True, text=True, encoding='utf-8',
                                errors='replace', timeout=90)
    except FileNotFoundError as error:
        raise AwsError('Install the AWS CLI first.') from error
    except subprocess.TimeoutExpired as error:
        raise AwsError('AWS did not respond. Check network access and your AWS session.') from error
    if result.returncode:
        message = result.stderr.strip()
        if any(value in message.lower() for value in ('session has expired', 'sso session', 'unable to locate credentials')):
            raise AwsError('AWS sign-in has expired or is missing. Run aws login, then rerun this command.')
        raise AwsError(message or 'AWS request failed.')
    try:
        return json.loads(result.stdout)
    except ValueError as error:
        raise AwsError('AWS returned an unexpected response.') from error


def discover_log_group(service, region):
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,40}', service):
        raise ValueError('Invalid App Runner service name.')
    groups = aws('logs', 'describe-log-groups', '--log-group-name-prefix', f'/aws/apprunner/{service}/', region=region)
    names = [item['logGroupName'] for item in groups.get('logGroups', [])
             if item['logGroupName'].endswith('/application')]
    if len(names) != 1:
        raise AwsError('Expected one application log group. Supply --log-group with the exact group name.')
    return validate_log_group(names[0])


def verify_queries(body, region, seconds=172800):
    """Bounded, opt-in syntax/data checks. Each check is a chargeable log query."""
    end = int(time.time())
    reports = []
    for widget in body['widgets']:
        if widget['type'] != 'log':
            continue
        props = widget['properties']
        source, query = props['query'].split('\n| ', 1)
        group = source.removeprefix("SOURCE '").removesuffix("'")
        started = aws('logs', 'start-query', '--log-group-name', group,
                      '--start-time', str(end - seconds), '--end-time', str(end),
                      '--query-string', query, region=region)
        query_id = started['queryId']
        deadline = time.monotonic() + 90
        while True:
            result = aws('logs', 'get-query-results', '--query-id', query_id, region=region)
            status = result.get('status')
            if status == 'Complete':
                reports.append({'title': props['title'], 'status': status,
                                'rows': len(result.get('results', [])),
                                'bytes_scanned': result.get('statistics', {}).get('bytesScanned', 0),
                                'results': result.get('results', [])})
                break
            if status not in ('Scheduled', 'Running'):
                raise AwsError('Query validation failed for ' + props['title'] + ': ' + str(status))
            if time.monotonic() >= deadline:
                aws('logs', 'stop-query', '--query-id', query_id, region=region)
                raise AwsError('Query validation timed out for ' + props['title'])
            time.sleep(1)
    return reports


def save_private_json(value, stem):
    folder = ROOT / 'local_runs' / 'analytics'
    folder.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    target = folder / (stem + '-' + stamp + '.json')
    with target.open('x', encoding='utf-8') as output:
        json.dump(value, output, indent=2)
        output.write('\n')
    return target


def install(args):
    identity = aws('sts', 'get-caller-identity', region=args.region)
    if args.expected_account and identity.get('Account') != args.expected_account:
        raise AwsError('Wrong AWS account; no dashboard changed. Check --expected-account.')
    log_group = args.log_group or discover_log_group(args.service, args.region)
    body = build_dashboard(log_group, args.region)
    existing = aws('cloudwatch', 'list-dashboards', '--dashboard-name-prefix', args.name, region=args.region)
    found = any(item['DashboardName'] == args.name for item in existing.get('DashboardEntries', []))
    if found and not args.replace:
        raise AwsError('Dashboard already exists. Use --replace only if you want to replace it; a backup will be saved.')
    if found:
        previous = aws('cloudwatch', 'get-dashboard', '--dashboard-name', args.name, region=args.region)
        backup = save_private_json(json.loads(previous['DashboardBody']), args.name + '-backup')
        print('Saved previous dashboard to ' + str(backup))
    if args.verify_queries:
        report = verify_queries(body, args.region)
        path = save_private_json(report, args.name + '-query-check')
        print('Validated ' + str(len(report)) + ' queries; private results: ' + str(path))
    generated = save_private_json(body, args.name + '-dashboard')
    response = aws('cloudwatch', 'put-dashboard', '--dashboard-name', args.name,
                   '--dashboard-body', 'file://' + str(generated.resolve()), region=args.region)
    messages = response.get('DashboardValidationMessages', [])
    if messages:
        raise AwsError('AWS returned dashboard validation messages: ' + json.dumps(messages))
    saved = aws('cloudwatch', 'get-dashboard', '--dashboard-name', args.name, region=args.region)
    if json.loads(saved['DashboardBody']) != body:
        raise AwsError('Saved dashboard did not match the generated definition. Inspect the dashboard before relying on it.')
    print('Private dashboard saved: ' + dashboard_url(args.name, args.region))
    print('No App Runner, website, IAM, billing-preference or log-retention settings changed.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    create = sub.add_parser('install', help='One-time private AWS dashboard setup; no container rebuild.')
    create.add_argument('--name', default=DEFAULT_NAME)
    create.add_argument('--service', default='PDFtoHTML')
    create.add_argument('--region', default=DEFAULT_REGION, type=validate_region)
    create.add_argument('--log-group', type=validate_log_group)
    create.add_argument('--expected-account', default='900119715649')
    create.add_argument('--replace', action='store_true', help='Replace an existing dashboard after saving its definition.')
    create.add_argument('--verify-queries', action='store_true', help='Run each query once over 48h before installation (query charges apply).')
    build = sub.add_parser('build', help='Generate a definition offline, without AWS requests.')
    build.add_argument('--log-group', required=True, type=validate_log_group)
    build.add_argument('--region', default=DEFAULT_REGION, type=validate_region)
    build.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    try:
        if args.command == 'install':
            if not re.fullmatch(r'[A-Za-z0-9_-]{1,255}', args.name):
                raise ValueError('Invalid dashboard name.')
            install(args)
        else:
            with args.output.open('x', encoding='utf-8') as output:
                json.dump(build_dashboard(args.log_group, args.region), output, indent=2)
                output.write('\n')
            print(args.output)
    except (AwsError, ValueError, OSError) as error:
        parser.exit(1, str(error) + '\n')


if __name__ == '__main__':
    main()
