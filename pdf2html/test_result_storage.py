from contextlib import redirect_stdout
from datetime import timedelta
import io
import json
from pathlib import Path
import tempfile
from unittest import TestCase, main
from unittest.mock import patch

from botocore.exceptions import ClientError
from botocore.response import StreamingBody
from boto3.exceptions import S3UploadFailedError

import layout_compare
import local_server as server
import result_storage as storage
import test_usage_analytics as analytics_tests


class FakeS3:
    def __init__(self):
        self.objects = {}
        self.fail_write = None
        self.fail_reads = False

    def put_object(self, Bucket, Key, Body, **headers):
        if self.fail_write and self.fail_write in Key:
            raise ClientError({'Error': {'Code': 'AccessDenied'}}, 'PutObject')
        assert headers.get('ServerSideEncryption') == 'AES256'
        assert 'ACL' not in headers
        self.objects[Key] = (bytes(Body), headers)

    def upload_file(self, filename, bucket, key, ExtraArgs):
        self.put_object(Bucket=bucket, Key=key, Body=Path(filename).read_bytes(), **ExtraArgs)

    def get_object(self, Bucket, Key, Range=None):
        if self.fail_reads:
            raise ClientError({'Error': {'Code': 'AccessDenied'}}, 'GetObject')
        if Key not in self.objects:
            raise ClientError({'Error': {'Code': 'NoSuchKey'}}, 'GetObject')
        data, headers = self.objects[Key]
        size = len(data)
        result = {'ContentType': headers.get('ContentType'), 'ETag': '"test"'}
        if Range:
            left, right = Range[6:].split('-')
            if not left:
                start, end = max(0, size-int(right)), size-1
            else:
                start, end = int(left), min(int(right), size-1) if right else size-1
            if start > end or start >= size:
                raise ClientError({'Error': {'Code': 'InvalidRange'}}, 'GetObject')
            data = data[start:end+1]
            result['ContentRange'] = f'bytes {start}-{end}/{size}'
        result.update(Body=StreamingBody(io.BytesIO(data), len(data)), ContentLength=len(data))
        return result

    def head_object(self, **kwargs):
        result = self.get_object(**kwargs)
        result.pop('Body').close()
        return result


class StoreTests(TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.job = Path(self.temp.name)/('a'*32)
        (self.job/'output'/'images').mkdir(parents=True)
        (self.job/'input').mkdir()
        (self.job/'output'/'reader.html').write_text('<html><img src="images/figure.png"></html>')
        (self.job/'output'/'images'/'figure.png').write_bytes(b'PNG-test')
        (self.job/'input'/'original.pdf').write_bytes(b'PRIVATE PDF')
        (self.job/'job_status.json').write_text('PRIVATE metadata')
        (self.job/'article_results.zip').write_bytes(b'ZIP-test')
        self.fake = FakeS3()
        self.store = storage.S3ResultStore('private-results', client=self.fake)
        self.status = {'status': 'completed', 'created_at': storage.utcnow().isoformat(),
                       'expires_at': (storage.utcnow()+timedelta(days=30)).isoformat(),
                       'retention_basis': 'first_conversion', 'transaction_number': '952125',
                       'preview_filename': 'output/reader.html', 'zip_filename': 'article_results.zip',
                       'source_sha256': 'f'*64, 'zip_path': 'PRIVATE local path', 'traceback': 'PRIVATE'}

    def test_publish_only_outputs_and_zip_and_private_record_then_lookup(self):
        self.store.publish(self.job, self.status)
        keys = list(self.fake.objects)
        self.assertTrue(keys[-1].endswith('/record.json'))
        self.assertFalse(any('/input/' in key or key.endswith('job_status.json') for key in keys))
        record = self.store.get_record(self.job.name)
        self.assertNotIn('zip_path', record['status'])
        self.assertNotIn('traceback', record['status'])
        self.assertEqual(self.store.find('952125', 'full', 'f'*64)[0], self.job.name)
        self.assertIsNone(self.store.find('952125', 'full', 'e'*64))
        self.assertIsNone(self.store.find('952125', 'tables', 'f'*64))
        self.assertIsNone(self.store.object(record, 'job_status.json'))
        response = self.store.object(record, 'output/images/figure.png')
        self.assertEqual(response['Body'].read(), b'PNG-test')
        response['Body'].close()

    def test_expiry_enforced_before_lifecycle_deletes_objects(self):
        self.store.publish(self.job, self.status)
        with patch.object(storage, 'utcnow', return_value=storage.timestamp(self.status['expires_at'])):
            with self.assertRaises(storage.ResultExpired):
                self.store.get_record(self.job.name)
            with self.assertRaises(storage.ResultExpired):
                self.store.find('952125', 'full', 'f'*64)
        self.assertTrue(self.fake.objects)

    def test_storage_failure_never_looks_like_missing_cache(self):
        self.fake.fail_reads = True
        with self.assertRaises(storage.StorageUnavailable):
            self.store.find('952125', 'full', 'f'*64)

    def test_managed_transfer_failure_is_a_storage_error(self):
        with patch.object(self.fake, 'upload_file', side_effect=S3UploadFailedError('PRIVATE details')):
            with self.assertRaises(storage.StorageUnavailable) as result:
                self.store.publish(self.job, self.status)
        self.assertNotIn('PRIVATE', str(result.exception))

    def test_failed_publication_has_no_complete_record_and_dangling_index_fails_closed(self):
        for failed in ('/files/', '/deliveries/', '/record.json'):
            self.fake.objects.clear()
            self.fake.fail_write = failed
            with self.assertRaises(storage.StorageUnavailable):
                self.store.publish(self.job, self.status)
            self.assertIsNone(self.store.get_record(self.job.name))
            if failed == '/record.json':
                with self.assertRaises(storage.StorageUnavailable):
                    self.store.find('952125', 'full', 'f'*64)

    def test_paths_and_prefix_are_scoped(self):
        for name in ('input/original.pdf', 'job_status.json', '../x', 'output/../x',
                     'output//x', 'output/%2e%2e/x', 'output\\x', '/output/x'):
            self.assertFalse(storage.artifact_name(name), name)
        for prefix in ('', '/', '../', 'completed//bad', 'completed/../bad'):
            with self.assertRaises(storage.StorageUnavailable):
                storage.S3ResultStore('private-results', prefix=prefix, client=self.fake)

    def test_missing_or_corrupt_artifacts_do_not_trigger_regeneration(self):
        self.store.publish(self.job, self.status)
        record = self.store.get_record(self.job.name)
        del self.fake.objects[self.store.job_key(self.job.name)+'/files/output/reader.html']
        with self.assertRaises(storage.StorageUnavailable):
            self.store.object(record, 'output/reader.html')

    def test_optional_delivery_mode_requires_original_delivery_date(self):
        with patch.dict('os.environ', RESULTS_RETENTION_BASIS='delivery'):
            with self.assertRaises(storage.RetentionError):
                storage.retention()
            for value in ('yesterday', '2026-01-01T00:00:00', (storage.utcnow()+timedelta(days=1)).isoformat()):
                with self.assertRaises(storage.RetentionError):
                    storage.retention(value)
            delivered = storage.utcnow()-timedelta(days=10)
            self.assertEqual(storage.timestamp(storage.retention(delivered.isoformat())['expires_at']), delivered+timedelta(days=30))
            with self.assertRaises(storage.ResultExpired):
                storage.retention((storage.utcnow()-timedelta(days=31)).isoformat())

    def test_first_conversion_is_default_and_disabled_storage_creates_no_client(self):
        fixed = storage.utcnow()
        with patch.dict('os.environ', clear=True), patch.object(storage, 'utcnow', return_value=fixed):
            result = storage.retention()
            self.assertEqual(result['retention_basis'], 'first_conversion')
            self.assertEqual(storage.timestamp(result['expires_at']), fixed+timedelta(days=30))
        with patch.dict('os.environ', RESULTS_RETENTION_BASIS='first_conversion'):
            self.assertEqual(storage.retention()['retention_basis'], 'first_conversion')
        with patch.dict('os.environ', RESULTS_S3_ENABLED='false'), patch.object(storage, '_configured_store') as factory:
            self.assertIsNone(storage.get_store())
            factory.assert_not_called()


class S3RouteTests(TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)/'first-instance'
        self.root.mkdir()
        self.fake = FakeS3()
        self.store = storage.S3ResultStore('private-results', client=self.fake)
        self.addCleanup(patch.stopall)
        patch.object(server, 'RUNS_DIR', self.root).start()
        patch.object(server, 'get_store', return_value=self.store).start()
        patch.dict('os.environ', RESULTS_RETENTION_BASIS='first_conversion',
                   ACCESSIBLE_HTML_ANALYTICS_ENABLED='false', AWS_EC2_METADATA_DISABLED='true').start()
        patch.object(server.threading, 'Thread', analytics_tests.ImmediateThread).start()
        patch.object(server, '_analytics_usage', return_value={}).start()
        self.pipeline = patch.object(server, '_run_pipeline', side_effect=lambda **kw: analytics_tests.ServerAnalyticsTests.fake_pipeline(self, **kw)).start()
        patch('socket.create_connection', side_effect=AssertionError('Network forbidden')).start()
        self.client = server.app.test_client()

    def submit(self, transaction='952125', content=b'%PDF-test'):
        return self.client.post('/api/jobs', data={'pdf_file': (io.BytesIO(content), 'PRIVATE.pdf'),
                                                  'transaction_number': transaction})

    def test_completed_job_survives_fresh_empty_instance_and_reuses_without_ai(self):
        first = self.submit()
        self.assertEqual(first.status_code, 202)
        job = first.json['job_id']
        self.assertEqual([path.name for path in (self.root/job).iterdir()], ['job_status.json'])
        new_root = Path(self.tmp.name)/'new-instance'
        new_root.mkdir()
        with patch.object(server, 'RUNS_DIR', new_root):
            status = self.client.get('/api/jobs/'+job)
            self.assertEqual(status.json['status'], 'completed')
            self.assertEqual(status.json['storage'], 's3')
            for private in ('source_sha256', 'transaction_number', 'zip_path', 'traceback'):
                self.assertNotIn(private, status.json['details'])
            result = self.client.get('/api/jobs/'+job+'/result')
            self.assertEqual(result.status_code, 200)
            preview = self.client.get(f'/runs/{job}/output/remediated.html')
            self.assertEqual(preview.status_code, 200)
            self.assertIn(b'<html>', preview.data)
            self.assertEqual(preview.headers['Cache-Control'], 'no-store')
            preview.close()
            download = self.client.get(f'/runs/{job}/download')
            self.assertEqual(download.status_code, 200)
            self.assertTrue(download.data.startswith(b'PK'))
            download.close()
            reused = self.submit()
            self.assertEqual(reused.status_code, 200)
            self.assertEqual(reused.json['job_id'], job)
            self.assertEqual(reused.json['expires_at'], first.json['expires_at'])
        self.assertEqual(self.pipeline.call_count, 1)

    def test_cache_requires_matching_pdf_and_no_path_or_transaction_only_lookup(self):
        first = self.submit()
        for payload in ({'transaction_number':'952125'}, {'pdf_path':'/app/private.pdf','transaction_number':'952125'}):
            self.assertEqual(self.client.post('/api/jobs', json=payload).status_code, 400)
        second = self.submit(content=b'%PDF-different')
        self.assertEqual(second.status_code, 202)
        self.assertNotEqual(first.json['job_id'], second.json['job_id'])
        self.assertEqual(self.pipeline.call_count, 2)

    def test_storage_outage_does_not_start_paid_pipeline(self):
        self.fake.fail_reads = True
        response = self.submit()
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.headers['Retry-After'], '30')
        self.pipeline.assert_not_called()

    def test_upload_failure_never_reports_completion(self):
        self.fake.fail_write = '/files/'
        first = self.submit()
        status = self.client.get('/api/jobs/'+first.json['job_id'])
        self.assertEqual(status.json['status'], 'failed')
        self.assertNotIn('viewer_url', status.json)
        job = first.json['job_id']
        self.assertEqual(self.client.get(f'/runs/{job}/output/remediated.html').status_code, 409)
        self.assertEqual(self.client.get(f'/runs/{job}/download').status_code, 409)

    def test_expired_html_assets_metadata_and_zip_return_gone_even_with_local_files(self):
        first = self.submit()
        job = first.json['job_id']
        record = self.store.get_record(job)
        (self.root/job/'output').mkdir()
        (self.root/job/'output'/'remediated.html').write_text('A stale local copy must not bypass expiry.')
        with patch.object(storage, 'utcnow', return_value=storage.timestamp(record['status']['expires_at'])):
            for suffix in ('/api/jobs/'+job, '/api/jobs/'+job+'/result',
                           f'/runs/{job}/output/remediated.html', f'/runs/{job}/download'):
                self.assertEqual(self.client.get(suffix).status_code, 410)
            self.assertEqual(self.submit().status_code, 410)
        self.assertEqual(self.pipeline.call_count, 1)

    def test_scratch_cleanup_does_not_touch_an_outside_directory(self):
        other = Path(self.tmp.name)/'outside'/('b'*32)
        (other/'output').mkdir(parents=True)
        target = other/'output'/'keep.html'
        target.write_text('keep')
        (other/'job_status.json').write_text(json.dumps({'status':'completed', 'storage':'s3'}))
        server._cleanup_completed_scratch(other)
        self.assertTrue(target.exists())

    def test_private_paths_rejected_and_head_range_supported(self):
        first = self.submit()
        job = first.json['job_id']
        for name in ('job_status.json', 'input/PRIVATE.pdf', 'output/../job_status.json'):
            self.assertEqual(self.client.get(f'/runs/{job}/{name}').status_code, 404)
        response = self.client.head(f'/runs/{job}/output/remediated.html')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, b'')
        response = self.client.get(f'/runs/{job}/output/remediated.html', headers={'Range':'bytes=0-5'})
        self.assertEqual(response.status_code, 206)
        self.assertEqual(response.data, b'<html>')
        response.close()
        response = self.client.get(f'/runs/{job}/output/remediated.html', headers={'Range':'bytes=1-2,4-5'})
        self.assertEqual(response.status_code, 416)

    def test_strict_delivery_mode_rejects_missing_date_before_ai(self):
        with patch.dict('os.environ', RESULTS_RETENTION_BASIS='delivery'):
            self.assertEqual(self.submit().status_code, 400)
        self.pipeline.assert_not_called()

    def test_sync_form_results_are_also_durable(self):
        response = self.client.post('/process', data={'pdf_file':(io.BytesIO(b'%PDF-test'), 'PRIVATE.pdf')})
        self.assertEqual(response.status_code, 200)
        job = next(self.root.iterdir()).name
        self.assertIsNotNone(self.store.get_record(job))

    def test_analytics_stays_pseudonymous_after_instance_replacement(self):
        browser = '12345678-1234-4123-8123-123456789abc'
        with patch.dict('os.environ', ACCESSIBLE_HTML_ANALYTICS_ENABLED='true', ANALYTICS_ID_KEY='ab'*32), redirect_stdout(io.StringIO()) as stream:
            first = self.client.post('/api/jobs', data={
                'pdf_file':(io.BytesIO(b'%PDF-test'), 'PRIVATE.pdf'), 'transaction_number':'952125', 'reader_client_id':browser})
            job = first.json['job_id']
            with patch.object(server, 'RUNS_DIR', Path(self.tmp.name)/'empty-instance'):
                self.client.get(f'/runs/{job}/output/remediated.html', headers={'X-Accessible-Reader-Id':browser}).close()
        events = [json.loads(line) for line in stream.getvalue().splitlines() if line.startswith('{"analytics"')]
        completed = next(event for event in events if event['event']=='job_completed')
        served = next(event for event in events if event['event']=='html_response')
        self.assertEqual(completed['document_id'], served['document_id'])
        self.assertEqual(completed['browser_id'], served['browser_id'])
        self.assertEqual(served['html_kind'], 'selected_preview')
        self.assertNotIn('PRIVATE', stream.getvalue())
        self.assertNotIn(browser, stream.getvalue())

    def test_corrupt_manifest_and_missing_saved_page_fail_closed(self):
        first = self.submit()
        job = first.json['job_id']
        key = self.store.job_key(job)+'/record.json'
        record = self.store.get_record(job)
        del self.fake.objects[self.store.job_key(job)+'/files/output/remediated.html']
        self.assertEqual(self.client.get(f'/runs/{job}/output/remediated.html').status_code, 503)
        record['files']['output/remediated.html']['size'] = 'bad'
        raw, headers = self.fake.objects[key]
        self.fake.objects[key] = (json.dumps(record).encode(), headers)
        self.assertEqual(self.client.get('/api/jobs/'+job).status_code, 503)
        self.assertEqual(self.pipeline.call_count, 1)


if __name__ == '__main__':
    main()
