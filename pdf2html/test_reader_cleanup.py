import unittest
from reader_cleanup import prepare_cleanup, join_lines


def block(text, y=10):
    return {'bbox': (10,y,100,y+10), 'lines': [{'spans': [{'text': text}]}]}


class CleanupTests(unittest.TestCase):
    def test_repetition_requires_margins_and_multiple_pages(self):
        pages = [[block('Journal 1 of 3'), block('Repeated body', 200)],
                 [block('Journal 2 of 3'), block('Repeated body', 200)],
                 [block('Journal 3 of 3'), block('Repeated body', 200)]]
        retained, changes = prepare_cleanup(pages, [800]*3)
        self.assertEqual(len(retained), 1)
        self.assertEqual(len(changes), 3)
        self.assertNotIn('reader_cleanup_role', pages[0][1])

    def test_uncertain_hyphens_preserved(self):
        self.assertEqual(join_lines('a genera-', 'tors example', {'generators'}), 'a generators example')
        self.assertEqual(join_lines('a wave-', 'energy device', set()), 'a wave- energy device')
        self.assertEqual(join_lines('x-', 'ray', {'xray'}), 'x- ray')

    def test_one_page_and_body_metadata_not_removed(self):
        b = block('Published: important body text', 300)
        retained, changes = prepare_cleanup([[b]], [800])
        self.assertEqual(changes, [])
        self.assertEqual(retained, [])


if __name__ == '__main__':
    unittest.main()
