"""Safe, structured failure reasons for public responses and private analytics."""

FAILURE_MESSAGES = {
    'bda_timeout': 'BDA exceeded the configured waiting limit; the AWS job may still be running.',
    'bda_configuration_invalid': 'BDA_TIMEOUT_SECONDS must be an integer from 30 to 3600.',
    'bda_job_failed': 'BDA reported a terminal processing failure.',
    'bda_status_poll_failed': 'The BDA job status request failed.',
    'bda_invalid_status': 'BDA returned a missing or unsupported job status.',
    'bda_missing_invocation': 'BDA did not return a supported invocation identifier.',
    'document_page_limit': 'The PDF exceeds the 100-page document limit.',
    'pdf_unreadable': 'We could not read this PDF. Please upload a readable, unencrypted PDF.',
    'pdf_encrypted': 'This PDF requires a password. Please upload an unlocked copy.',
    'pdf_empty': 'This PDF has no pages. Please upload a PDF with 1 to 100 pages.',
    'processing_timeout': 'A processing operation timed out.',
    'storage_error': 'A file or storage operation failed; inspect restricted application diagnostics.',
    'validation_error': 'An input or configuration value was invalid; inspect restricted application diagnostics.',
    'aws_request_failed': 'An AWS request failed.',
    'processing_error': 'An unexpected processing error occurred; inspect restricted application diagnostics.',
}
AWS_ERROR_CODES = {'AccessDenied', 'AccessDeniedException', 'ThrottlingException', 'TooManyRequestsException',
                   'ValidationException', 'InternalServerException', 'InternalServerError',
                   'ServiceUnavailableException', 'ResourceNotFoundException', 'ServiceQuotaExceededException',
                   'ExpiredToken', 'ExpiredTokenException', 'UnrecognizedClientException', 'InvalidSignatureException'}
BDA_STATUSES = {'Created', 'InProgress', 'Pending', 'Running', 'Queued', 'Success', 'Succeeded', 'SUCCEEDED',
                'COMPLETED', 'Complete', 'Completed', 'ClientError', 'ServiceError', 'Failed', 'Failure', 'FAILED'}


def failure_message(fields):
    code = fields.get('error_code')
    if code == 'bda_timeout' and type(fields.get('bda_timeout_seconds')) is int:
        return (f"BDA did not finish within the configured {fields['bda_timeout_seconds']}-second waiting limit. "
                'The AWS job may still be running.')
    if code == 'document_page_limit' and type(fields.get('document_pages')) is int:
        return (f"This PDF has {fields['document_pages']} pages. We can remediate documents of up to 100 pages. "
                'Please split it into PDFs of 100 pages or fewer and submit each separately.')
    message = FAILURE_MESSAGES.get(code, FAILURE_MESSAGES['processing_error'])
    if fields.get('bda_last_status') in BDA_STATUSES:
        message += f" Last BDA status: {fields['bda_last_status']}."
    if fields.get('aws_error_code') in AWS_ERROR_CODES:
        message += f" AWS error: {fields['aws_error_code']}."
    return message


class BDAError(RuntimeError):
    def __init__(self, code, status=None, aws_code=None):
        self.safe_fields = {'error_code': code, 'error_stage': 'bda'}
        if status in BDA_STATUSES:
            self.safe_fields['bda_last_status'] = status
        if aws_code in AWS_ERROR_CODES:
            self.safe_fields['aws_error_code'] = aws_code
        super().__init__(failure_message(self.safe_fields))


class BDATimeoutError(TimeoutError):
    def __init__(self, seconds, status=None):
        self.safe_fields = {'error_code': 'bda_timeout', 'error_stage': 'bda', 'bda_timeout_seconds': seconds}
        if status in BDA_STATUSES:
            self.safe_fields['bda_last_status'] = status
        super().__init__(failure_message(self.safe_fields))


class PDFValidationError(ValueError):
    def __init__(self, code='pdf_unreadable', pages=None):
        self.safe_fields = {'error_code': code, 'error_stage': 'upload'}
        if pages is not None:
            self.safe_fields.update(document_pages=pages, max_document_pages=100)
        super().__init__(failure_message(self.safe_fields))


def aws_error_code(error):
    response = getattr(error, 'response', None)
    detail = response.get('Error') if isinstance(response, dict) else None
    code = detail.get('Code') if isinstance(detail, dict) else None
    return code if code in AWS_ERROR_CODES else None


def failure_details(error):
    chain, seen = [], set()
    while isinstance(error, BaseException) and id(error) not in seen and len(chain) < 20:
        seen.add(id(error))
        chain.append(error)
        error = error.__cause__ or error.__context__
    for exc in chain:
        if isinstance(exc, (BDAError, BDATimeoutError, PDFValidationError)):
            category = ('timeout' if isinstance(exc, TimeoutError) else 'validation_error'
                        if isinstance(exc, ValueError) or exc.safe_fields['error_code'] == 'bda_configuration_invalid'
                        else 'processing_error')
            return dict(exc.safe_fields, error_category=category)
    for exc in chain:
        if isinstance(exc, TimeoutError):
            return {'error_category': 'timeout', 'error_code': 'processing_timeout', 'error_stage': 'processing'}
        code = aws_error_code(exc)
        if code:
            return {'error_category': 'processing_error', 'error_code': 'aws_request_failed',
                    'error_stage': 'processing', 'aws_error_code': code}
    category = ('storage_error' if any(isinstance(e, OSError) for e in chain) else
                'validation_error' if any(isinstance(e, ValueError) for e in chain) else 'processing_error')
    return {'error_category': category, 'error_code': category, 'error_stage': 'processing'}
