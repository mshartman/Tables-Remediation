"""Page-count preflight before any paid processing; no document text extraction."""
from pypdf import PdfReader

from content_accessibility_utility_on_aws.utils.job_errors import PDFValidationError

MAX_DOCUMENT_PAGES = 100


def validate_pdf_pages(source):
    stream = hasattr(source, 'seek')
    try:
        if stream:
            source.seek(0)
        reader = PdfReader(source, strict=False)
        if reader.is_encrypted and not reader.decrypt(''):
            raise PDFValidationError('pdf_encrypted')
        pages = len(reader.pages)
        if pages < 1:
            raise PDFValidationError('pdf_empty')
        if pages > MAX_DOCUMENT_PAGES:
            raise PDFValidationError('document_page_limit', pages)
        return pages
    except PDFValidationError:
        raise
    except Exception as exc:
        raise PDFValidationError() from exc
    finally:
        if stream:
            source.seek(0)
