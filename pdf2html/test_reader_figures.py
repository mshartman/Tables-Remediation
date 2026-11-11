import unittest
from reader_figures import figure_groups


def caption(value='Figure 1. A diagram.', bbox=(20,220,500,240)):
    return {'bbox': bbox, 'lines': [{'spans': [{'text': value}]}]}


class FigureTests(unittest.TestCase):
    def test_single_and_panels(self):
        left = {'bbox': (20,100,230,200), 'image': b'left'}
        right = {'bbox': (250,100,500,200), 'image': b'right'}
        groups, consumed, report = figure_groups([left,right,caption()],800)
        self.assertEqual(groups, {0:[0,1,2]})
        self.assertEqual(consumed, {0,1,2})
        self.assertEqual(report[0]['status'], 'associated_needs_review')

    def test_prose_scan_and_distant_image_are_not_consumed(self):
        img = {'bbox': (20,100,500,200), 'image': b'image'}
        for blocks in ([img,caption('Intervening prose.'),caption()],
                       [img,caption(bbox=(20,500,500,520))],
                       [{'bbox':(0,0,600,780),'image':b'scan'},caption(bbox=(20,785,500,795))]):
            groups, consumed, _ = figure_groups(blocks,800)
            self.assertFalse(groups)
            self.assertFalse(consumed)

    def test_caption_text_required(self):
        img = {'bbox':(20,100,500,200),'image':b'image'}
        self.assertFalse(figure_groups([img,caption('Figure 1 shows results.')],800)[0])


if __name__ == '__main__':
    unittest.main()
