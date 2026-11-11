import unittest
from reader_audit import audit_reader


class AuditTests(unittest.TestCase):
    def test_no_false_conformance_claim(self):
        report = audit_reader('<html lang="en"><title>Example</title><main><h1>Example</h1></main></html>')
        self.assertEqual(report['status'], 'manual_review_required')
        self.assertEqual(report['findings'], [])

    def test_detects_structural_and_content_gaps(self):
        report = audit_reader('<main><p id="x">a</p><p id="x">b</p><a href="#absent">Go</a><img><table><tr><td>1</td></tr></table><p aria-describedby="missing">Text</p></main>', [{}])
        codes = {item['code'] for item in report['findings']}
        self.assertTrue({'missing_title', 'missing_language', 'document_heading', 'duplicate_ids', 'broken_fragment', 'missing_image_alternative', 'table_without_headers', 'broken_aria_reference', 'table_extraction_review'} <= codes)

    def test_authored_alt_is_not_assumed_missing(self):
        report = audit_reader('<img alt=""><img alt="Description"><img alt="Draft" data-alt-status="ai-generated">')
        codes = {item['code'] for item in report['findings']}
        self.assertNotIn('missing_image_alternative', codes)
        self.assertIn('ai_alternative_review', codes)
