"""Private S3 storage for completed reader artifacts, not a public website bucket."""
from datetime import datetime, timedelta, timezone
from functools import lru_cache
import hashlib
import hmac
import json
import mimetypes
import os
import re

import boto3
from boto3.exceptions import S3UploadFailedError
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError


JOB_ID = re.compile(r'(?:[0-9a-f]{12}|[0-9a-f]{32})')
STATUS_FIELDS = {'status', 'source_type', 'original_filename', 'created_at', 'updated_at',
                 'transaction_number', 'remediation_mode', 'preview_filename', 'zip_filename',
                 'issues_found', 'issues_needing_remediation', 'issues_remediated',
                 'issues_selected_for_remediation', 'expires_at', 'retention_basis', 'storage'}
STATUS_FIELDS |= {'source_sha256'}


class StorageUnavailable(Exception):
    pass


class ResultExpired(Exception):
    pass


class RetentionError(ValueError):
    pass


def utcnow():
    return datetime.now(timezone.utc)


def timestamp(value):
    if not isinstance(value, str) or len(value) > 64:
        raise ValueError('Invalid timestamp')
    result = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if result.tzinfo is None:
        raise ValueError('Timestamp needs a time zone')
    return result.astimezone(timezone.utc)


def expired(status):
    if not status.get('expires_at'):
        return False
    try:
        return timestamp(status['expires_at']) <= utcnow()
    except (ValueError, TypeError):
        return True  # Never expose a stored result with an unreadable expiry.


def retention(delivered_at=None):
    """Thirty days from the first submission by default; never extend on reads."""
    basis = os.getenv('RESULTS_RETENTION_BASIS', 'first_conversion').strip()
    now = utcnow()
    if basis == 'delivery':
        try:
            start = timestamp(delivered_at)
        except (TypeError, ValueError):
            raise RetentionError('A timezone-qualified delivery_at timestamp is required for 30-day delivery retention.') from None
        if start > now:
            raise RetentionError('delivery_at cannot be in the future.')
    elif basis == 'first_conversion':
        start = now
    else:
        raise StorageUnavailable('Invalid result retention configuration.')
    end = start + timedelta(days=30)
    if end <= now:
        raise ResultExpired()
    return {'expires_at': end.isoformat(), 'retention_basis': basis}


def artifact_name(value):
    if (not isinstance(value, str) or not value or len(value) > 1024 or
            any(ord(char) < 32 for char in value) or '\\' in value or '%' in value):
        return False
    parts = value.split('/')
    if any(part in {'', '.', '..'} for part in parts):
        return False
    # Inputs and private job/cache records must never be served as artifacts.
    return (len(parts) > 1 and parts[0] == 'output' or
            len(parts) == 1 and value.endswith('_results.zip'))


class S3ResultStore:
    def __init__(self, bucket, prefix='completed-html/v1', client=None):
        if not isinstance(bucket, str) or not bucket or '/' in bucket:
            raise StorageUnavailable('Invalid results bucket configuration.')
        if (not isinstance(prefix, str) or len(prefix) > 120 or
                not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9/_-]*', prefix) or
                any(not part for part in prefix.split('/'))):
            raise StorageUnavailable('Invalid results prefix configuration.')
        self.bucket, self.prefix = bucket, prefix
        self.client = client or boto3.client('s3', config=Config(
            connect_timeout=5, read_timeout=30, retries={'max_attempts': 2, 'mode': 'standard'}))

    def job_key(self, job_id):
        if not isinstance(job_id, str) or not JOB_ID.fullmatch(job_id):
            raise StorageUnavailable('Invalid stored job identifier.')
        return f'{self.prefix}/jobs/{job_id}'

    def index_key(self, transaction, workflow, source_sha256):
        value = json.dumps([str(transaction), workflow, source_sha256], separators=(',', ':'))
        digest = hashlib.sha256(value.encode()).hexdigest()
        return f'{self.prefix}/deliveries/{digest}.json'

    def _json(self, key):
        try:
            response = self.client.get_object(Bucket=self.bucket, Key=key)
            body = response['Body']
            try:
                raw = body.read(4 * 1024 * 1024 + 1)
            finally:
                body.close()
            if len(raw) > 4 * 1024 * 1024:
                raise ValueError('Oversize metadata')
            value = json.loads(raw)
            if not isinstance(value, dict):
                raise ValueError('Invalid metadata')
            return value
        except ClientError as error:
            # AccessDenied is not evidence that a result does not exist.
            if error.response.get('Error', {}).get('Code') in {'NoSuchKey', '404', 'NotFound'}:
                return None
            raise StorageUnavailable('Stored results are temporarily unavailable.') from None
        except (BotoCoreError, ValueError, KeyError, OSError):
            raise StorageUnavailable('Stored results are temporarily unavailable.') from None

    def _put_json(self, key, value):
        self.client.put_object(Bucket=self.bucket, Key=key,
                               Body=json.dumps(value, allow_nan=False).encode(), ContentType='application/json',
                               CacheControl='no-store', ServerSideEncryption='AES256')

    def get_record(self, job_id):
        record = self._json(self.job_key(job_id) + '/record.json')
        if record is None:
            return None
        try:
            status, files = record['status'], record['files']
            if (record.get('schema_version') != 1 or record.get('job_id') != job_id or
                    status.get('status') != 'completed' or not isinstance(files, dict) or
                    not status.get('expires_at') or status.get('preview_filename') not in files or
                    status.get('zip_filename') not in files or
                    any(not artifact_name(name) for name in files)):
                raise ValueError('Invalid completed record')
            for info in files.values():
                if (not isinstance(info, dict) or type(info.get('size')) is not int or info['size'] < 0 or
                        not isinstance(info.get('content_type'), str) or
                        not re.fullmatch(r'[A-Za-z0-9.+-]+/[A-Za-z0-9.+-]+', info['content_type'])):
                    raise ValueError('Invalid artifact metadata')
            if expired(status):
                raise ResultExpired()
            return record
        except (KeyError, TypeError, AttributeError, ValueError):
            raise StorageUnavailable('Stored results are temporarily unavailable.') from None

    def find(self, transaction, workflow, source_sha256):
        if not transaction or not source_sha256:
            return None
        index = self._json(self.index_key(transaction, workflow, source_sha256))
        if index is None:
            return None
        try:
            if not index.get('expires_at'):
                raise ValueError('Missing index expiry')
            if expired(index):
                raise ResultExpired()
            record = self.get_record(index['job_id'])
            if record is None:
                # Inconsistent storage must not silently cause a paid regeneration.
                raise StorageUnavailable('A stored conversion needs recovery. Please retry later.')
            status = record['status']
            if status.get('transaction_number') != transaction or status.get('remediation_mode', 'full') != workflow:
                raise ValueError('Index does not match job')
            if not source_sha256 or not hmac.compare_digest(status.get('source_sha256', ''), source_sha256):
                return None
            return record['job_id'], status
        except (KeyError, ValueError, TypeError):
            raise StorageUnavailable('Stored results are temporarily unavailable.') from None

    def publish(self, job_dir, status):
        """Files first; the record is the final commit marker for a complete job."""
        job_id = job_dir.name
        base = self.job_key(job_id)
        if not status.get('expires_at'):
            raise StorageUnavailable('A retention deadline is required.')
        if expired(status):
            raise ResultExpired()
        files = {}
        paths = sorted((job_dir / 'output').rglob('*')) + sorted(job_dir.glob('*_results.zip'))
        try:
            for path in paths:
                if path.is_symlink():
                    raise ValueError('Symlink artifact')
                if not path.is_file():
                    continue
                name = path.relative_to(job_dir).as_posix()
                if not artifact_name(name) or not path.resolve().is_relative_to(job_dir.resolve()):
                    raise ValueError('Invalid artifact')
                content_type = mimetypes.guess_type(name)[0] or 'application/octet-stream'
                self.client.upload_file(str(path), self.bucket, f'{base}/files/{name}', ExtraArgs={
                    'ContentType': content_type, 'CacheControl': 'no-store', 'ServerSideEncryption': 'AES256'})
                files[name] = {'size': path.stat().st_size, 'content_type': content_type}
            if status.get('preview_filename') not in files or status.get('zip_filename') not in files:
                raise ValueError('Missing completed output')
            clean = {key: value for key, value in status.items() if key in STATUS_FIELDS}
            clean.update(status='completed', storage='s3')
            if expired(clean):
                raise ResultExpired()
            record = {'schema_version': 1, 'job_id': job_id, 'status': clean, 'files': files}
            if clean.get('transaction_number'):
                self._put_json(self.index_key(clean['transaction_number'], clean.get('remediation_mode', 'full'), clean.get('source_sha256')),
                               {'job_id': job_id, 'expires_at': clean['expires_at']})
            self._put_json(f'{base}/record.json', record)
            return clean
        except (BotoCoreError, ClientError, S3UploadFailedError, OSError, ValueError, TypeError):
            raise StorageUnavailable('Completed results could not be saved. Please contact staff before retrying conversion.') from None

    def object(self, record, name, head=False, byte_range=None):
        if expired(record['status']):
            raise ResultExpired()
        if not artifact_name(name) or name not in record['files']:
            return None
        params = {'Bucket': self.bucket, 'Key': f"{self.job_key(record['job_id'])}/files/{name}"}
        if byte_range and not head:
            params['Range'] = byte_range
        try:
            return self.client.head_object(**params) if head else self.client.get_object(**params)
        except ClientError as error:
            code = error.response.get('Error', {}).get('Code')
            if code in {'NoSuchKey', '404', 'NotFound'}:
                raise StorageUnavailable('A stored artifact is missing. Please contact staff before retrying conversion.') from None
            if code == 'InvalidRange':
                raise ValueError('Invalid byte range') from None
            raise StorageUnavailable('Stored results are temporarily unavailable.') from None
        except BotoCoreError:
            raise StorageUnavailable('Stored results are temporarily unavailable.') from None


@lru_cache(maxsize=2)
def _configured_store(bucket, prefix):
    try:
        return S3ResultStore(bucket, prefix)
    except BotoCoreError:
        raise StorageUnavailable('Result storage credentials or configuration are unavailable.') from None


def get_store():
    if os.getenv('RESULTS_S3_ENABLED', 'false').lower().strip() not in {'true', '1', 'yes', 'on'}:
        return None
    return _configured_store(os.getenv('RESULTS_S3_BUCKET', '').strip(),
                             os.getenv('RESULTS_S3_PREFIX', 'completed-html/v1').strip())
