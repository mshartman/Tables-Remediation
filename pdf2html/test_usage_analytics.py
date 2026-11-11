from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import layout_compare  # Configure the local PDF dependency paths before server imports.
import local_server as server
from usage_analytics import JobTimer, correlation, cost_metrics, provider_metrics, record_event
from analytics_report import read_events, summarize


class ImmediateThread:
    def __init__(self, target, **kwargs):
        self.target = target

    def start(self):
        self.target()


class AnalyticsUnitTests(unittest.TestCase):
    def test_correlations_require_key_and_valid_client_id(self):
        client = '12345678-1234-4123-8123-123456789abc'
        with patch.dict('os.environ', ANALYTICS_ID_KEY='', ACCESSIBLE_HTML_ANALYTICS_ENABLED='true'):
            self.assertEqual(correlation(client, '952125', 'a'*12), {})
        with patch.dict('os.environ', ANALYTICS_ID_KEY='ab'*32, ACCESSIBLE_HTML_ANALYTICS_ENABLED='true'):
            first = correlation(client, '952125', 'a'*12)
            self.assertEqual(first, correlation(client.upper(), '952125', 'b'*12))
            self.assertEqual(len(first['browser_id']), 67)
            self.assertNotIn(client, json.dumps(first))
            self.assertNotIn('952125', json.dumps(first))
            self.assertEqual(first['document_scope'], 'illiad_delivery')
            self.assertNotIn('browser_id', correlation('username@example.org', '952125'))
            self.assertNotIn('browser_id', correlation('12345678-1234-1123-8123-123456789abc', '952125'))
            job = correlation(client, job_id='a'*12)
            self.assertEqual(job['document_scope'], 'job')
            self.assertNotEqual(first['document_id'], job['document_id'])
        with patch.dict('os.environ', ANALYTICS_ID_KEY='cd'*32):
            self.assertNotEqual(first['browser_id'], correlation(client)['browser_id'])

    def test_raw_ids_never_enter_allowlisted_events(self):
        with redirect_stdout(io.StringIO()) as stream:
            record_event('job_completed', browser_id='12345678-1234-4123-8123-123456789abc',
                         document_id='952125', document_scope='invented', cost_coverage='free')
        event = json.loads(stream.getvalue())
        for field in ('browser_id', 'document_id', 'document_scope', 'cost_coverage'):
            self.assertNotIn(field, event)

    def test_costs_use_configured_model_rates_and_cached_tokens(self):
        rates = {'bda_per_page': .01, 'bedrock': {'test-model': {'input_per_million': 1, 'output_per_million': 2}},
                 'openai': {'test-model': {'input_per_million': 1, 'cached_input_per_million': .1, 'output_per_million': 2}}}
        aws = {'bda_usage': {'total_pages_processed': 3}, 'bedrock_usage': {'calls_by_model': {
            'test-model': {'input_tokens': 1000, 'output_tokens': 500}}}}
        openai = {'model': 'test-model', 'usage': [{'input_tokens': 1000, 'output_tokens': 500,
                                                  'input_tokens_details': {'cached_tokens': 200}}]}
        with patch.dict('os.environ', ANALYTICS_RATES_JSON=json.dumps(rates)):
            result = cost_metrics(aws, openai)
            self.assertAlmostEqual(result['estimated_reported_ai_cost_usd'], .03382)
            self.assertEqual(result['cost_coverage'], 'partial')
            self.assertEqual(cost_metrics(None, {'model':'unknown', 'usage':openai['usage']}), {'cost_coverage':'unavailable'})
        for config in ('{}', 'null', '{invalid', '{"bda_per_page":-1}'):
            with patch.dict('os.environ', ANALYTICS_RATES_JSON=config):
                self.assertNotIn('estimated_reported_ai_cost_usd', cost_metrics(aws, openai))

    def test_private_breakdown_opt_in_and_cost_not_counted_again_on_reads(self):
        with patch.dict('os.environ', ANALYTICS_ID_KEY='ab'*32), redirect_stdout(io.StringIO()) as stream:
            context = correlation('12345678-1234-4123-8123-123456789abc', '952125')
            record_event('job_completed', **context, bda_pages=4, estimated_reported_ai_cost_usd=.05)
            record_event('job_cache_hit', **context)
            record_event('html_response', **context, html_kind='selected_preview', http_status=200)
        events = [json.loads(line) for line in stream.getvalue().splitlines()]
        self.assertNotIn('private_breakdown', summarize(events))
        result = summarize(events, details=True)
        self.assertEqual(result['identified_browsers'], 1)
        self.assertEqual(result['identified_delivered_documents'], 1)
        bucket = result['private_breakdown']['browsers'][context['browser_id']]
        self.assertEqual(bucket['bda_pages'], 4)
        self.assertEqual(bucket['estimated_reported_ai_cost_usd'], .05)
        self.assertEqual(bucket['preview_deliveries'], 1)

    def test_only_allowlisted_nonidentifying_values_are_emitted(self):
        stream = io.StringIO()
        with patch.dict('os.environ', ACCESSIBLE_HTML_ANALYTICS_ENABLED='true'), redirect_stdout(stream):
            record_event('job_completed', channel='api', workflow='full', bedrock_calls=3,
                         filename='PRIVATE', ip='PRIVATE', transaction_number='PRIVATE',
                         job_id='PRIVATE', model='PRIVATE', issues_found=float('nan'), openai_attempts=True)
            record_event('not_a_supported_event', filename='PRIVATE')
        event = json.loads(stream.getvalue())
        self.assertEqual(event['bedrock_calls'], 3)
        self.assertNotIn('PRIVATE', stream.getvalue())
        self.assertNotIn('issues_found', event)
        self.assertNotIn('openai_attempts', event)
        self.assertEqual(len(event['event_id']), 32)

    def test_disabled_and_broken_output_are_fail_open(self):
        with patch.dict('os.environ', ACCESSIBLE_HTML_ANALYTICS_ENABLED='false'), redirect_stdout(io.StringIO()) as stream:
            self.assertFalse(record_event('job_started', channel='api'))
            self.assertEqual(stream.getvalue(), '')
        with patch.dict('os.environ', ACCESSIBLE_HTML_ANALYTICS_ENABLED='true'), patch('sys.stdout.write', side_effect=OSError('PRIVATE')):
            self.assertFalse(record_event('job_started', channel='api'))

    def test_queue_and_processing_are_separate_and_terminal_event_is_single(self):
        stream = io.StringIO()
        with patch.dict('os.environ', ACCESSIBLE_HTML_ANALYTICS_ENABLED='true'), redirect_stdout(stream), patch('usage_analytics.time.monotonic', side_effect=[100, 102, 107]):
            timer = JobTimer('api', 'full')
            timer.start()
            timer.complete({'audit_result': {'issues': []}})
            timer.fail(RuntimeError('PRIVATE'))
        events = [json.loads(line) for line in stream.getvalue().splitlines()]
        self.assertEqual([e['event'] for e in events], ['job_queued', 'job_started', 'job_completed'])
        self.assertEqual(events[-1]['queue_ms'], 2000)
        self.assertEqual(events[-1]['processing_ms'], 5000)
        self.assertEqual(events[-1]['elapsed_ms'], 7000)

    def test_provider_totals_omit_raw_reports_and_count_openai_attempts_separately(self):
        result = provider_metrics(
            {'bda_usage': {'total_pages_processed': 4, 'project_arn': 'PRIVATE'},
             'bedrock_usage': {'total_calls': 2, 'total_input_tokens': 100, 'call_details': ['PRIVATE']}},
            {'calls': 3, 'failed': 1, 'generated': 2, 'model': 'PRIVATE',
             'usage': [{'input_tokens': 20, 'output_tokens': 10}, {'input_tokens': 30, 'output_tokens': 5}]})
        self.assertEqual(result['bda_pages'], 4)
        self.assertEqual(result['bedrock_calls'], 2)
        self.assertEqual(result['openai_attempts'], 3)
        self.assertEqual(result['openai_responses'], 2)
        self.assertEqual(result['openai_input_tokens'], 50)
        self.assertNotIn('PRIVATE', json.dumps(result))

    def test_export_summary_deduplicates_and_ignores_polling_and_noise(self):
        stream = io.StringIO()
        with patch.dict('os.environ', ACCESSIBLE_HTML_ANALYTICS_ENABLED='true'), redirect_stdout(stream):
            record_event('submission_requested', channel='api')
            record_event('job_completed', processing_ms=4000, bda_pages=3, bedrock_calls=2)
            record_event('html_response', html_kind='selected_preview', http_status=200)
            record_event('html_response', html_kind='selected_preview', http_status=304)
        events = [json.loads(line) for line in stream.getvalue().splitlines()]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'export.json'
            path.write_text(json.dumps({'events': [{'message': json.dumps(e)} for e in events + events[:1]]}), encoding='utf-8')
            result = summarize(read_events(path))
        self.assertEqual(result['events']['submission_requested'], 1)
        self.assertEqual(result['duplicate_events_ignored'], 1)
        self.assertEqual(result['events']['preview_deliveries'], 1)
        self.assertEqual(result['reported_ai_usage']['bda_pages'], 3)
        self.assertEqual(result['completed_job_timings_ms']['processing_ms']['mean'], 4000)

    def test_summary_ignores_bad_events_and_accepts_mixed_jsonl(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'export.jsonl'
            path.write_text('ordinary diagnostic text\n' + json.dumps({'analytics':'pdf2html_usage', 'schema_version':1,
                             'event':'job_failed', 'event_id':'a'*32, 'timestamp':'invalid'}) + '\n', encoding='utf-8')
            result = summarize(read_events(path))
        self.assertEqual(result['invalid_analytics_events_ignored'], 1)
        self.assertEqual(result['events'], {})


class ServerAnalyticsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.addCleanup(patch.stopall)
        patch.object(server, 'RUNS_DIR', self.root).start()
        patch.dict('os.environ', ACCESSIBLE_HTML_ANALYTICS_ENABLED='true', AWS_EC2_METADATA_DISABLED='true').start()
        patch.object(server.threading, 'Thread', ImmediateThread).start()
        patch.object(server.SessionUsageTracker, '_instance', None).start()
        patch('socket.create_connection', side_effect=AssertionError('Network forbidden')).start()
        self.pipeline = patch.object(server, '_run_pipeline', side_effect=self.fake_pipeline).start()
        self.client = server.app.test_client()

    def fake_pipeline(self, **kwargs):
        output = kwargs['output_dir'];output.mkdir(parents=True, exist_ok=True)
        target = output/'remediated.html';target.write_text('<html><title>PRIVATE</title><p>PRIVATE</p></html>')
        (output/'remediation_report.html').write_text('<html>Report</html>')
        (output/'openai-image-report.json').write_text(json.dumps({'calls': 2, 'generated': 2, 'failed': 0,
                                                                 'model': 'PRIVATE', 'usage': [{'input_tokens': 15, 'output_tokens': 8}]}))
        server.SessionUsageTracker._instance = SimpleNamespace(
            bda_usage={'total_documents_processed':1, 'total_pages_processed':4, 'project_arn':'PRIVATE'},
            bedrock_usage={'total_calls':2, 'total_input_tokens':100, 'total_output_tokens':20})
        return {'conversion_result':{'html_path':str(target)}, 'audit_result':{'issues':[]},
                'remediation_result':{'remediated_html_path':str(target), 'issues_remediated':1},
                'issues_selected_for_remediation':1}

    def submit(self):
        return self.client.post('/api/jobs', data={'pdf_file':(io.BytesIO(b'%PDF-test'), 'PRIVATE.pdf'),
                                                  'transaction_number':'PRIVATE-transaction'},
                                headers={'User-Agent':'PRIVATE-agent', 'Authorization':'Bearer PRIVATE-key'})

    def captured(self, stream):
        return [json.loads(line) for line in stream.getvalue().splitlines() if line.startswith('{"analytics"')]

    def test_success_is_counted_once_with_provider_totals_and_without_identity(self):
        with redirect_stdout(io.StringIO()) as stream:
            response = self.submit()
        self.assertEqual(response.status_code, 202)
        events = self.captured(stream)
        self.assertEqual([e['event'] for e in events], ['submission_requested','job_queued','job_started','job_completed'])
        self.assertEqual(events[-1]['bda_pages'], 4)
        self.assertEqual(events[-1]['openai_attempts'], 2)
        self.assertNotIn('PRIVATE', stream.getvalue())
        self.assertNotIn(response.json['job_id'], stream.getvalue())

    def test_cache_reuse_does_not_count_another_conversion(self):
        with redirect_stdout(io.StringIO()) as stream:
            first = self.submit()
            second = self.client.post('/api/jobs', json={'transaction_number':'PRIVATE-transaction'})
        events = self.captured(stream)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(first.json['job_id'], second.json['job_id'])
        self.assertEqual(sum(e['event']=='job_completed' for e in events), 1)
        self.assertEqual(sum(e['event']=='submission_requested' for e in events), 2)
        self.assertEqual([e['cache_state'] for e in events if e['event']=='job_cache_hit'], ['completed'])
        self.assertEqual(self.pipeline.call_count, 1)

    def test_reusing_a_queued_job_is_not_reported_as_a_completed_job(self):
        class PendingThread(ImmediateThread):
            def start(self):pass
        with patch.object(server.threading, 'Thread', PendingThread), redirect_stdout(io.StringIO()) as stream:
            self.submit()
            response = self.client.post('/api/jobs', json={'transaction_number':'PRIVATE-transaction'})
        events = self.captured(stream)
        self.assertEqual(response.status_code, 200)
        self.assertEqual([e['cache_state'] for e in events if e['event']=='job_cache_hit'], ['queued'])
        self.assertFalse(any(e['event'] in {'job_started','job_completed'} for e in events))
        self.pipeline.assert_not_called()

    def test_polls_health_preflights_and_head_do_not_inflate_usage(self):
        with redirect_stdout(io.StringIO()):
            response = self.submit()
        job = response.json['job_id']
        with redirect_stdout(io.StringIO()) as stream:
            for _ in range(4):
                self.client.get('/api/jobs/'+job)
                self.client.get('/api/jobs/'+job+'/result')
            self.client.get('/health')
            self.client.options('/api/jobs')
            self.client.head(f'/runs/{job}/output/remediated.html').close()
            self.client.get(f'/runs/{job}/output/missing.png')
        self.assertEqual(self.captured(stream), [])

    def test_preview_report_missing_html_and_zip_have_distinct_events(self):
        with redirect_stdout(io.StringIO()):
            response = self.submit()
        job = response.json['job_id']
        with redirect_stdout(io.StringIO()) as stream:
            for name in ('remediated.html','remediation_report.html','missing.html'):
                self.client.get(f'/runs/{job}/output/{name}').close()
            self.client.get(f'/runs/{job}/download').close()
        events = self.captured(stream)
        self.assertEqual([(e['html_kind'],e['http_status']) for e in events if e['event']=='html_response'],
                         [('selected_preview',200),('other_html',200),('other_html',404)])
        self.assertEqual(events[-1]['event'], 'zip_response')

    def test_validation_and_oversize_errors_do_not_count_jobs(self):
        with redirect_stdout(io.StringIO()) as stream:
            self.client.post('/api/jobs', data={'pdf_file':(io.BytesIO(b'x'), 'PRIVATE.txt')})
            with patch.dict(server.app.config, MAX_CONTENT_LENGTH=128):
                response = self.client.post('/api/jobs', data={'pdf_file':(io.BytesIO(b'x'*1024), 'PRIVATE.pdf')})
        self.assertEqual(response.status_code, 413)
        events = self.captured(stream)
        self.assertEqual([e['http_status'] for e in events if e['event']=='submission_error'], [400,413])
        self.assertFalse(any(e['event']=='job_queued' for e in events))
        self.assertNotIn('PRIVATE', stream.getvalue())

    def test_failure_logs_category_not_exception_and_keeps_partial_usage(self):
        def fail(**kwargs):
            self.fake_pipeline(**kwargs)
            raise RuntimeError('PRIVATE document and credentials')
        self.pipeline.side_effect = fail
        with redirect_stdout(io.StringIO()) as stream:
            response = self.submit()
        events = self.captured(stream)
        self.assertEqual(events[-1]['event'], 'job_failed')
        self.assertEqual(events[-1]['error_category'], 'processing_error')
        self.assertEqual(events[-1]['bedrock_calls'], 2)
        self.assertNotIn('PRIVATE', stream.getvalue())
        self.assertEqual(server._read_job_status(self.root/response.json['job_id'])['status'], 'failed')

    def test_packaging_failure_is_failed_not_completed(self):
        with patch.object(server, '_zip_directory', side_effect=OSError('PRIVATE disk path')), redirect_stdout(io.StringIO()) as stream:
            self.submit()
        events = self.captured(stream)
        self.assertEqual(events[-1]['event'], 'job_failed')
        self.assertEqual(events[-1]['error_category'], 'storage_error')
        self.assertFalse(any(e['event']=='job_completed' for e in events))
        self.assertNotIn('PRIVATE', stream.getvalue())

    def test_worker_start_failure_is_recorded_without_exception_text(self):
        job = 'a'*12
        job_dir = self.root/job
        input_dir, _ = server._ensure_run_dirs(job_dir)
        server._create_job(job_dir, 'PRIVATE.pdf', 'upload')
        with patch.object(server.threading, 'Thread', side_effect=RuntimeError('PRIVATE worker error')), redirect_stdout(io.StringIO()) as stream:
            with self.assertRaises(RuntimeError):
                server._start_background_job(job, input_dir/'PRIVATE.pdf', 'PRIVATE.pdf', '', '', '', '', True, True)
        self.assertEqual([e['event'] for e in self.captured(stream)], ['job_queued','job_failed'])
        self.assertEqual(server._read_job_status(job_dir)['status'], 'failed')
        self.assertNotIn('PRIVATE', stream.getvalue())

    def test_provider_snapshot_is_taken_before_singleton_lock_is_released(self):
        class Lock:
            def __enter__(self):return self
            def __exit__(self, *args):server.SessionUsageTracker._instance.bedrock_usage['total_calls'] = 999
        with patch.object(server, 'PROCESS_LOCK', Lock()), redirect_stdout(io.StringIO()) as stream:
            self.submit()
        completed = next(e for e in self.captured(stream) if e['event']=='job_completed')
        self.assertEqual(completed['bedrock_calls'], 2)

    def test_form_upload_and_legacy_preview_are_measured(self):
        with redirect_stdout(io.StringIO()) as stream:
            response = self.client.post('/process', data={'pdf_file':(io.BytesIO(b'%PDF-test'), 'PRIVATE.pdf')})
            job = next(self.root.iterdir()).name
            self.client.get(f'/runs/{job}/output/remediated.html').close()
        self.assertEqual(response.status_code, 200)
        events = self.captured(stream)
        self.assertTrue(any(e['event']=='job_completed' and e['channel']=='form' for e in events))
        self.assertEqual(events[-1]['html_kind'], 'legacy_preview')

    def test_local_table_lab_does_not_reuse_aws_singleton_totals(self):
        with patch.object(server, '_run_local_table_pipeline', side_effect=self.fake_pipeline), redirect_stdout(io.StringIO()) as stream:
            response = self.client.post('/api/table-jobs', data={'pdf_file':(io.BytesIO(b'%PDF-test'), 'PRIVATE.pdf')})
        self.assertEqual(response.status_code, 202)
        completed = next(e for e in self.captured(stream) if e['event']=='job_completed')
        self.assertEqual(completed['workflow'], 'local_tables')
        self.assertEqual(completed['bedrock_usage_available'], 0)
        self.assertNotIn('bedrock_calls', completed)

    def test_disabled_analytics_does_not_change_responses(self):
        with patch.dict('os.environ', ACCESSIBLE_HTML_ANALYTICS_ENABLED='false'), redirect_stdout(io.StringIO()) as stream:
            response = self.submit()
        self.assertEqual(response.status_code, 202)
        self.assertEqual(stream.getvalue(), '')

    def test_broken_analytics_output_does_not_fail_conversion(self):
        with patch('sys.stdout.write', side_effect=OSError('PRIVATE')):
            response = self.submit()
        self.assertEqual(response.status_code, 202)
        self.assertEqual(server._read_job_status(self.root/response.json['job_id'])['status'], 'completed')

    def test_corrupt_openai_report_keeps_aws_totals(self):
        self.fake_pipeline(output_dir=self.root)
        (self.root/'openai-image-report.json').write_text('invalid')
        result = server._analytics_usage(self.root)
        self.assertEqual(result['bedrock_calls'], 2)
        self.assertEqual(result['openai_usage_available'], 0)

    def test_no_public_analytics_reporting_route_is_added(self):
        self.assertEqual(self.client.get('/api/analytics').status_code, 404)

    def test_viewer_records_current_browser_not_original_requester(self):
        first_client = '12345678-1234-4123-8123-123456789abc'
        second_client = '12345678-1234-4123-9123-123456789abc'
        with patch.dict('os.environ', ANALYTICS_ID_KEY='ab'*32), redirect_stdout(io.StringIO()) as stream:
            response = self.client.post('/api/jobs', data={'pdf_file':(io.BytesIO(b'%PDF-test'), 'PRIVATE.pdf'),
                'transaction_number':'952125', 'reader_client_id':first_client})
            job = response.json['job_id']
            self.client.get(f'/runs/{job}/output/remediated.html', headers={'X-Accessible-Reader-Id':second_client}).close()
            self.client.post('/api/jobs', json={'transaction_number':'952125', 'reader_client_id':second_client})
        events = self.captured(stream)
        completed = next(e for e in events if e['event']=='job_completed')
        delivered = next(e for e in events if e['event']=='html_response')
        reused = next(e for e in events if e['event']=='job_cache_hit')
        self.assertNotEqual(completed['browser_id'], delivered['browser_id'])
        self.assertEqual(reused['browser_id'], delivered['browser_id'])
        self.assertEqual(completed['document_id'], delivered['document_id'])
        self.assertNotIn(first_client, stream.getvalue())
        self.assertNotIn(second_client, stream.getvalue())
        self.assertNotIn('952125', stream.getvalue())
        status = server._read_job_status(self.root/job)
        self.assertNotIn('browser_id', status)
        self.assertNotIn('document_id', status)

    def test_preflight_allows_browser_header_and_invalid_job_ids_are_rejected(self):
        with patch.object(server, 'ALLOWED_ORIGIN', 'https://example.org'):
            response = self.client.options('/runs/'+'a'*12+'/output/remediated.html', headers={
                'Origin':'https://example.org', 'Access-Control-Request-Headers':'X-Accessible-Reader-Id'})
        self.assertLess(response.status_code, 400)
        self.assertIn('X-Accessible-Reader-Id', response.headers['Access-Control-Allow-Headers'])
        for path in ('/api/jobs/..', '/api/jobs/INVALID/result', '/runs/../output/remediated.html', '/runs/not-a-job/download'):
            self.assertEqual(self.client.get(path).status_code, 404)


if __name__ == '__main__':
    unittest.main()
