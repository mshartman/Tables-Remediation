"""Offline checks for dashboard scope, privacy, costs and safe installation."""
import argparse
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import usage_dashboard as dashboard


GROUP = '/aws/apprunner/PDFtoHTML/' + 'a' * 32 + '/application'


class DashboardTests(unittest.TestCase):
    def test_application_group_only_and_no_query_injection(self):
        self.assertEqual(dashboard.validate_log_group(GROUP), GROUP)
        for value in (GROUP.replace('/application', '/service'), GROUP + "' | limit 1",
                      'unrelated', GROUP.replace('a' * 32, 'placeholder')):
            with self.assertRaises(ValueError):
                dashboard.build_dashboard(value)

    def test_queries_select_analytics_schema_and_correct_group(self):
        body = dashboard.build_dashboard(GROUP)
        queries = [w['properties']['query'] for w in body['widgets'] if w['type'] == 'log']
        self.assertEqual(len(queries), 12)
        for query in queries:
            if 'BDA job timed out after' in query:
                self.assertTrue(query.startswith("SOURCE '" + GROUP + "'\n| filter @message like /bedrock_client/"))
                self.assertNotIn('fields @message', query)
                continue
            self.assertTrue(query.startswith("SOURCE '" + GROUP + "'\n| " + dashboard.FILTER))
            self.assertNotIn('@message', query)
            self.assertNotIn('dedup', query)

    def test_failure_details_show_safe_reason_and_support_old_events(self):
        query = dashboard.queries()['failure_details']
        for field in ('error_message', 'error_code', 'error_stage', 'bda_timeout_seconds', 'bda_last_status', '@logStream'):
            self.assertIn(field, query)
        self.assertIn('coalesce(error_message', query)
        self.assertNotIn('@message', query)
        self.assertIn('by bin(1s), @logStream, wait_seconds', dashboard.queries()['legacy_bda_timeouts'])

    def test_supported_views_and_no_overlapping_widgets(self):
        widgets = dashboard.build_dashboard(GROUP)['widgets']
        for i, left in enumerate(widgets):
            self.assertLessEqual(left['x'] + left['width'], 24)
            if left['type'] == 'log':
                self.assertIn(left['properties']['view'], ('table', 'timeSeries', 'bar', 'pie'))
            for right in widgets[i + 1:]:
                overlaps = (left['x'] < right['x'] + right['width'] and
                            right['x'] < left['x'] + left['width'] and
                            left['y'] < right['y'] + right['height'] and
                            right['y'] < left['y'] + left['height'])
                self.assertFalse(overlaps)

    def test_costs_are_not_faked_for_missing_rates(self):
        for key in ('cost_total', 'cost_per_pdf'):
            query = dashboard.queries()[key]
            self.assertIn('coalesce(priced_samples, 0) > 0', query)
            self.assertIn('coalesce(priced_samples, 0) as priced_jobs', query)
            self.assertIn('"Unavailable"', query)
            self.assertIn('unpriced_jobs', query)
            self.assertIn('job_failed', query)

    def test_delivery_counts_exclude_errors_and_report_pages(self):
        query = dashboard.queries()['per_browser']
        self.assertIn('http_status in [200, 206]', query)
        self.assertIn('"selected_preview", "legacy_preview"', query)
        self.assertIn('ispresent(browser_id)', query)

    def test_daily_aggregation_does_not_redefine_its_ephemeral_fields(self):
        query = dashboard.queries()['daily_conversions']
        self.assertIn('sum(completed_flag) as completed', query)
        self.assertNotIn('sum(completed) as completed', query)
        self.assertNotIn('sum(failed) as failed', query)

    def test_per_pdf_is_private_and_does_not_treat_views_as_conversions(self):
        query = dashboard.queries()['per_pdf']
        self.assertIn('by document_id, document_scope', query)
        self.assertIn('if(event = "job_completed", 1, 0)', query)
        self.assertIn('if(event = "job_cache_hit", 1, 0)', query)
        for sensitive in ('transaction_number', 'filename', 'title', 'cookie', 'username', 'email'):
            self.assertNotIn(sensitive, query)

    def test_billing_uses_maximum_and_never_sums_cumulative_charges(self):
        metrics = [w for w in dashboard.build_dashboard(GROUP)['widgets'] if w['type'] == 'metric']
        self.assertEqual(len(metrics), 2)
        for widget in metrics:
            self.assertEqual(widget['properties']['region'], 'us-east-1')
            self.assertEqual(widget['properties']['stat'], 'Maximum')
            self.assertEqual(widget['properties']['period'], 21600)
            self.assertNotIn('SUM', json.dumps(widget))
            self.assertNotIn('FILL', json.dumps(widget))

    def test_no_auto_refresh_or_public_link(self):
        body = dashboard.build_dashboard(GROUP)
        self.assertEqual(body['start'], '-P7D')
        self.assertNotIn('refresh', body)
        self.assertNotIn('public', dashboard.dashboard_url())
        text = '\n'.join(w['properties']['markdown'] for w in body['widgets'] if w['type'] == 'text')
        self.assertIn('cannot be disabled', text)
        self.assertIn('account-wide', text)
        self.assertIn('not people', text)

    def args(self, **changes):
        values = dict(region='us-east-1', name='PDFtoHTML-Usage', service='PDFtoHTML',
                      expected_account='123456789012', log_group=GROUP,
                      replace=False, verify_queries=False)
        values.update(changes)
        return argparse.Namespace(**values)

    def test_wrong_account_cannot_write(self):
        with patch.object(dashboard, 'aws', return_value={'Account': '999999999999'}) as aws:
            with self.assertRaises(dashboard.AwsError):
                dashboard.install(self.args())
        self.assertEqual(aws.call_count, 1)

    def test_existing_dashboard_requires_explicit_replace(self):
        with patch.object(dashboard, 'aws', side_effect=[
                {'Account': '123456789012'}, {'DashboardEntries': [{'DashboardName': 'PDFtoHTML-Usage'}]}]) as aws:
            with self.assertRaises(dashboard.AwsError):
                dashboard.install(self.args())
        self.assertFalse(any(call.args[1] == 'put-dashboard' for call in aws.call_args_list))

    def test_install_roundtrip_and_windows_file_parameter(self):
        body = dashboard.build_dashboard(GROUP)
        def fake_aws(*args, **kwargs):
            if args[:2] == ('sts', 'get-caller-identity'):
                return {'Account': '123456789012'}
            if args[:2] == ('cloudwatch', 'list-dashboards'):
                return {'DashboardEntries': []}
            if args[:2] == ('cloudwatch', 'put-dashboard'):
                reference = args[args.index('--dashboard-body') + 1]
                self.assertTrue(reference.startswith('file://'))
                self.assertEqual(json.loads(Path(reference[7:]).read_text()), body)
                return {'DashboardValidationMessages': []}
            if args[:2] == ('cloudwatch', 'get-dashboard'):
                return {'DashboardBody': json.dumps(body)}
            self.fail('Unexpected AWS operation: ' + repr(args))
        with tempfile.TemporaryDirectory() as folder, patch.object(dashboard, 'ROOT', Path(folder)), patch.object(dashboard, 'aws', side_effect=fake_aws):
            dashboard.install(self.args())

    def test_query_verification_is_bounded(self):
        body = {'widgets': [next(w for w in dashboard.build_dashboard(GROUP)['widgets'] if w['type'] == 'log')]}
        with patch.object(dashboard, 'aws', side_effect=[{'queryId': 'example'}, {'status': 'Complete', 'results': []}]) as aws:
            report = dashboard.verify_queries(body, 'us-east-1', seconds=300)
        self.assertEqual(len(report), 1)
        args = aws.call_args_list[0].args
        start = int(args[args.index('--start-time') + 1])
        end = int(args[args.index('--end-time') + 1])
        self.assertEqual(end - start, 300)
        self.assertEqual(args[args.index('--log-group-name') + 1], GROUP)


if __name__ == '__main__':
    unittest.main()
