"""AI disclosures are deterministic, idempotent and never require model calls."""
import unittest
from unittest.mock import Mock, patch
from bs4 import BeautifulSoup

from content_accessibility_utility_on_aws.utils.alt_text import set_ai_alt
from content_accessibility_utility_on_aws.remediate.remediation_strategies import image_remediation as images
from image_descriptions import annotate_image_gaps


class AIAltLabelTests(unittest.TestCase):
    def test_label_is_canonical_and_not_duplicated(self):
        soup = BeautifulSoup('<img>', 'html.parser')
        for text in ('A chart of two groups.',
                     'AI generated alt text: A chart of two groups.',
                     '  ai generated alt text: AI generated alt text: A chart of two groups. '):
            set_ai_alt(soup.img, text)
            self.assertEqual(soup.img['alt'], 'AI generated alt text: A chart of two groups.')
            self.assertEqual(soup.img['data-alt-status'], 'ai-generated')

    def test_existing_ai_descriptions_get_label_when_reader_is_rendered(self):
        soup = BeautifulSoup('<main><img alt="A chart." data-alt-status="ai-generated">'
                             '<img alt="Authored description"><img alt="">'
                             '<img alt="Image description unavailable." data-alt-status="missing"></main>', 'html.parser')
        for _ in range(2):
            annotate_image_gaps(soup, soup.main)
        self.assertEqual([img['alt'] for img in soup.select('img')], [
            'AI generated alt text: A chart.', 'Authored description', '', 'Image description unavailable.'])

    def test_all_bedrock_remediation_success_paths_are_labeled(self):
        cases = [
            (images.remediate_missing_alt_text, ''),
            (images.remediate_empty_alt_text, ' alt=""'),
            (images.remediate_generic_alt_text, ' alt="Image"'),
            (images.remediate_long_alt_text, ' alt="' + 'Long description ' * 12 + '"'),
        ]
        for remediate, attributes in cases:
            with self.subTest(strategy=remediate.__name__):
                soup = BeautifulSoup('<figure><img src="figure.png"' + attributes + '></figure>', 'html.parser')
                issue = {'element': str(soup.img)}
                with (patch.object(images, 'generate_alt_text', return_value='A chart of two groups.') as generate,
                      patch.object(images, '_is_decorative_image', return_value=False)):
                    remediate(soup, issue, Mock())
                generate.assert_called_once()
                self.assertEqual(soup.img['alt'], 'AI generated alt text: A chart of two groups.')
                self.assertEqual(soup.img['data-alt-status'], 'ai-generated')


if __name__ == '__main__':
    unittest.main()
