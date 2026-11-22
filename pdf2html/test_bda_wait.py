"""BDA polling and diagnostic tests with fake time and no AWS requests."""
from contextlib import redirect_stdout
import io
import json
import unittest
from unittest.mock import Mock, patch

import layout_compare  # Load project-local test dependencies.
from botocore.exceptions import ClientError
from content_accessibility_utility_on_aws.pdf2html.services import bedrock_client as bda
from content_accessibility_utility_on_aws.utils.job_errors import BDAError, BDATimeoutError
from content_accessibility_utility_on_aws.utils.logging_helper import DocumentAccessibilityError
from usage_analytics import JobTimer, record_event


class Clock:
    def __init__(self):
        self.now = 0
    def read(self):
        return self.now
    def sleep(self, seconds):
        self.now += seconds


class BDAWaitTests(unittest.TestCase):
    def run_wait(self, responses, seconds=30):
        client = Mock()
        client.get_data_automation_status.side_effect = responses
        clock = Clock()
        with (patch.object(bda.time, 'monotonic', side_effect=clock.read),
              patch.object(bda.time, 'sleep', side_effect=clock.sleep),
              patch('socket.create_connection', side_effect=AssertionError('Network forbidden'))):
            return bda.wait_for_bda(client, 'private-invocation', seconds)

    def test_wait_config_default_valid_and_invalid(self):
        with patch.dict('os.environ', {}, clear=True):
            self.assertEqual(bda.bda_timeout_seconds(), 300)
        for value in ('30', '900', '3600'):
            with patch.dict('os.environ', BDA_TIMEOUT_SECONDS=value):
                self.assertEqual(bda.bda_timeout_seconds(), int(value))
        for value in ('', '0', '-1', '29', '3601', '3.5', 'inf', 'PRIVATE', '9'*10000):
            with patch.dict('os.environ', BDA_TIMEOUT_SECONDS=value), self.assertRaises(BDAError) as caught:
                bda.bda_timeout_seconds()
            self.assertNotIn('PRIVATE', str(caught.exception))

    def test_success_and_legacy_terminal_spellings(self):
        for status in ('Success', 'Succeeded', 'SUCCEEDED', 'COMPLETED', 'Complete', 'Completed'):
            self.assertEqual(self.run_wait([{'status':'Created'}, {'status':'InProgress'}, {'status':status}])['status'], status)

    def test_terminal_service_errors_are_not_retried_until_timeout(self):
        for status in ('ClientError', 'ServiceError', 'Failed', 'FAILED'):
            with self.assertRaises(BDAError) as caught:
                self.run_wait([{'status':status, 'errorMessage':'PRIVATE document text'}])
            self.assertEqual(caught.exception.safe_fields['error_code'], 'bda_job_failed')
            self.assertIn(status, str(caught.exception))
            self.assertNotIn('PRIVATE', str(caught.exception))

    def test_timeout_keeps_type_duration_and_last_status(self):
        with self.assertRaises(BDATimeoutError) as caught:
            self.run_wait([{'status':'InProgress'}] * 6)
        self.assertIsInstance(caught.exception, TimeoutError)
        self.assertEqual(caught.exception.safe_fields['bda_timeout_seconds'], 30)
        self.assertEqual(caught.exception.safe_fields['bda_last_status'], 'InProgress')
        self.assertIn('30-second', str(caught.exception))
        self.assertNotIn('Unknown', str(caught.exception))

    def test_access_denied_is_preserved_without_raw_aws_message(self):
        error = ClientError({'Error': {'Code':'AccessDeniedException', 'Message':'PRIVATE token and filename'}}, 'GetDataAutomationStatus')
        with self.assertRaises(BDAError) as caught:
            self.run_wait([error])
        self.assertIn('AccessDeniedException', str(caught.exception))
        self.assertNotIn('PRIVATE', str(caught.exception))

    def test_transient_poll_error_retries_without_resubmission(self):
        error = ClientError({'Error': {'Code':'ThrottlingException', 'Message':'PRIVATE'}}, 'GetDataAutomationStatus')
        self.assertEqual(self.run_wait([error, {'status':'Success'}])['status'], 'Success')

    def test_invalid_status_does_not_become_unknown(self):
        for response in ({}, {'status':[]}, {'status':'PRIVATE'}, None):
            with self.assertRaises(BDAError) as caught:
                self.run_wait([response])
            self.assertEqual(caught.exception.safe_fields['error_code'], 'bda_invalid_status')

    def test_bad_config_rejected_before_upload_or_submission(self):
        client = bda.ExtendedBDAClient.__new__(bda.ExtendedBDAClient)
        client.s3_client = Mock()
        client.bda_runtime_client = Mock()
        with patch.dict('os.environ', BDA_TIMEOUT_SECONDS='invalid'), self.assertRaises(BDAError):
            client.process_and_retrieve('unused.pdf', 'unused', {})
        client.s3_client.upload_file.assert_not_called()
        client.bda_runtime_client.invoke_data_automation_async.assert_not_called()

    def test_wrapped_timeout_is_identified_and_arbitrary_message_is_not_logged(self):
        try:
            try:
                raise BDATimeoutError(900, 'InProgress')
            except BDATimeoutError as original:
                raise DocumentAccessibilityError('PRIVATE filename and token') from original
        except DocumentAccessibilityError as wrapped:
            with redirect_stdout(io.StringIO()) as stream:
                timer = JobTimer('api', 'full')
                timer.start()
                timer.fail(wrapped)
        event = json.loads(stream.getvalue().splitlines()[-1])
        self.assertEqual(event['error_category'], 'timeout')
        self.assertEqual(event['error_code'], 'bda_timeout')
        self.assertIn('900-second', event['error_message'])
        self.assertNotIn('PRIVATE', stream.getvalue())

    def test_analytics_ignores_forged_error_text(self):
        with redirect_stdout(io.StringIO()) as stream:
            record_event('job_failed', error_code='bda_timeout', error_message='PRIVATE SECRET',
                         bda_timeout_seconds=300, bda_last_status='PRIVATE')
        event = json.loads(stream.getvalue())
        self.assertIn('300-second', event['error_message'])
        self.assertNotIn('PRIVATE', stream.getvalue())


if __name__ == '__main__':
    unittest.main()
