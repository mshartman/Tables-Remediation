import copy
import unittest

from layout_structure import plain, refine_headings, split_leading_headings


def line(text, y, font='OpaqueBody', size=10):
    box = (50, y, 280, y + size)
    return {'bbox': box, 'spans': [{'text': text, 'font': font, 'size': size, 'bbox': box, 'flags': 0}]}


def merged(title, y=100, font='OpaqueHeading', size=11):
    lines = [line(title, y, font, size),
             line('This is ordinary explanatory prose with enough supporting content', y + 18),
             line('to establish that the preceding label introduces a substantive paragraph.', y + 30)]
    return {'bbox': (50, y, 280, y + 40), 'lines': lines}


class LeadingHeadingTests(unittest.TestCase):
    def test_recurring_opaque_styles_split_without_losing_or_changing_text(self):
        pages = [[merged('Experimental design'), merged('Study outcomes', 250)]]
        original = copy.deepcopy(pages)
        result = split_leading_headings(pages, [800], 10)
        self.assertEqual(pages, original)
        self.assertEqual(len(result[0]), 4)
        self.assertEqual(' '.join(plain(b) for b in result[0]), ' '.join(plain(b) for b in pages[0]))
        refine_headings(result, [800], 10)
        self.assertEqual([b.get('heading_level') for b in result[0]], [2, None, 2, None])
        self.assertEqual(result[0][0]['bbox'], (50, 100, 280, 111))
        self.assertEqual(result[0][1]['bbox'], (50, 118, 280, 140))

    def test_multiline_lead_and_subordinate_family(self):
        first = merged('A major section', font='Generic-Bold')
        second = merged('Another major section', 250, 'Generic-Bold')
        sub = merged('A longer subsection', 400)
        sub['lines'].insert(1, line('continued on another line', 412, 'OpaqueHeading', 11))
        sub['lines'][2] = line('This is ordinary explanatory prose with enough supporting content', 430)
        sub['lines'][3] = line('to establish that this is substantive prose rather than metadata.', 442)
        sub['bbox'] = (50, 400, 280, 452)
        other = merged('Second subsection', 550)
        result = split_leading_headings([[first, second, sub, other]], [800], 10)
        refine_headings(result, [800], 10)
        headings = [(plain(b), b.get('heading_level')) for b in result[0] if b.get('heading_level')]
        self.assertEqual([level for _, level in headings], [2, 2, 3, 3])
        self.assertIn('continued on another line', headings[2][0])

    def test_single_occurrence_is_not_enough(self):
        pages = [[merged('An isolated emphasized phrase')]]
        self.assertEqual(split_leading_headings(pages, [800], 10), pages)

    def test_larger_recurring_parents_restore_hierarchy(self):
        pages = [[]]
        for title, child, y in [('Research methods', 'Participant selection', 120),
                                ('Research findings', 'Primary outcomes', 350)]:
            ln = line(title, y, 'OpaqueParent', 14)
            pages[0].append({'lines': [ln], 'bbox': ln['bbox']})
            pages[0].append(merged(child, y + 25))
        result = split_leading_headings(pages, [800], 10)
        refine_headings(result, [800], 10)
        self.assertEqual([b.get('heading_level') for b in result[0]], [2, 3, None, 2, 3, None])

    def test_one_larger_label_cannot_establish_parent_family(self):
        parent = line('An isolated large label', 120, 'OpaqueParent', 14)
        pages = [[{'lines': [parent], 'bbox': parent['bbox']},
                  merged('First recurring child', 145), merged('Second recurring child', 350)]]
        result = split_leading_headings(pages, [800], 10)
        refine_headings(result, [800], 10)
        self.assertNotEqual(result[0][0].get('heading_level'), 2)
        self.assertEqual([b.get('heading_level') for b in result[0][1:]], [2, None, 2, None])

    def test_whitespace_only_source_blocks_are_ignored(self):
        blank = line('   ', 400)
        pages = [[merged('Study procedure'), merged('Study results', 250),
                  {'lines': [blank], 'bbox': blank['bbox']}]]
        result = split_leading_headings(pages, [800], 10)
        refine_headings(result, [800], 10)
        self.assertEqual([b.get('heading_level') for b in result[0]], [2, None, 2, None, None])

    def test_caption_and_punctuated_sentence_are_not_headings(self):
        for title in ('Table 2. Observed values', 'Figure 1. Sampling sites', 'An emphasized sentence.'):
            with self.subTest(title=title):
                pages = [[merged(title), merged(title, 300)]]
                self.assertEqual(split_leading_headings(pages, [800], 10), pages)

    def test_inline_emphasis_and_body_sized_labels_are_not_split(self):
        first = merged('Partly emphasized prose')
        first['lines'][0]['spans'].append({**first['lines'][0]['spans'][0], 'text': ' in a regular font', 'font': 'OpaqueBody'})
        second = merged('Another partly emphasized line', 300)
        second['lines'][0]['spans'].append({**second['lines'][0]['spans'][0], 'text': ' with regular text', 'font': 'OpaqueBody'})
        for pages in ([[first, second]], [[merged('Same sized label', size=10), merged('Another label', 300, size=10)]]):
            self.assertEqual(split_leading_headings(pages, [800], 10), pages)

    def test_margin_metadata_and_short_affiliations_are_not_split(self):
        pages = [[merged('Metadata at top', 10), merged('More metadata at top', 10)]]
        self.assertEqual(split_leading_headings(pages, [800], 10), pages)
        pages = [[merged('Author One'), merged('Author Two', 300)]]
        for b in pages[0]:
            for ln in b['lines'][1:]:
                ln['spans'][0]['text'] = 'Short affiliation'
        self.assertEqual(split_leading_headings(pages, [800], 10), pages)


if __name__ == '__main__':
    unittest.main()
