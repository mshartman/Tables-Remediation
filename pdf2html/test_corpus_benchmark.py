import hashlib
from pathlib import Path
import tempfile
import unittest
from corpus_benchmark import partition,development_sample,prepare,extend_manifest,next_batch


class CorpusTests(unittest.TestCase):
    def test_new_batch_excludes_previously_selected_and_holdout(self):
        rows=partition(self.records(),known_reviewed=['1','2'])
        original={'records':rows,'seed':'fixed','development_sample_sha256':development_sample(rows)}
        new=next_batch(original,5)
        third=next_batch(new,5)
        self.assertFalse(set(new['development_sample_sha256'])&set(original['development_sample_sha256']))
        self.assertFalse(set(third['development_sample_sha256'])&(set(new['development_sample_sha256'])|set(original['development_sample_sha256'])))
        self.assertEqual(new['development_sample_sha256'],next_batch(original,5)['development_sample_sha256'])
        dev={r['sha256'] for r in rows if r['split']=='development' and not r['previously_reviewed']}
        self.assertTrue(set(new['development_sample_sha256'])<=dev)

    def test_unnamed_pdf_extension_and_immutable_extension(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);source=root/'source';source.mkdir()
            (source/'first.pdf').write_bytes(b'first')
            prepare(source,root/'initial',progress=lambda _:None)
            manifest=root/'initial'/'manifest.json';before=manifest.read_bytes()
            (source/'.pdf').write_bytes(b'%PDF-1.7 new document')
            result=extend_manifest(manifest,source/'.pdf',root/'complete')
            self.assertEqual(result['summary']['files_hashed'],2)
            self.assertEqual(manifest.read_bytes(),before)
            fresh=prepare(source,root/'fresh',progress=lambda _:None)
            self.assertEqual(fresh['summary']['files_found'],2)

    def records(self):
        return [{'filename':f'{i}.pdf','bytes':i+1,'sha256':hashlib.sha256(str(i).encode()).hexdigest()} for i in range(200)]

    def test_order_independent_and_duplicate_groups_stay_together(self):
        records=self.records()
        records.append(dict(records[0],filename='duplicate.pdf'))
        first=partition([dict(r) for r in records])
        second=partition([dict(r) for r in reversed(records)])
        self.assertEqual({r['filename']:r['split'] for r in first},{r['filename']:r['split'] for r in second})
        self.assertEqual(first[0]['split'],first[-1]['split'])

    def test_known_examples_and_duplicates_are_development(self):
        records=self.records();records.append(dict(records[0],filename='copy.pdf'))
        partition(records,known_reviewed=['0'])
        for record in (records[0],records[-1]):
            self.assertEqual(record['split'],'development')
            self.assertTrue(record['previously_reviewed'])
        self.assertTrue(any(r['split']=='holdout' for r in records))

    def test_sample_excludes_holdout_seen_and_duplicates(self):
        records=partition(self.records(),known_reviewed=['1','2','3'])
        selected=development_sample(records)
        self.assertEqual(len(selected),24)
        self.assertEqual(len(set(selected)),24)
        self.assertTrue(all(r['split']=='development' and not r['previously_reviewed'] for r in records if r['sha256'] in selected))
        self.assertEqual(selected,development_sample(list(reversed(records))))

    def test_read_only_inventory_and_no_overwrite(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);source=root/'source';source.mkdir()
            original=b'No parser is invoked by this inventory.'
            (source/'1.pdf').write_bytes(original);(source/'copy.pdf').write_bytes(original)
            result=prepare(source,root/'report',known_reviewed=['1'],progress=lambda _:None)
            self.assertEqual(result['summary']['unique_sha256'],1)
            self.assertEqual(result['summary']['exact_duplicate_copies'],1)
            self.assertEqual((source/'1.pdf').read_bytes(),original)
            with self.assertRaises(ValueError):prepare(source,root/'report')
            with self.assertRaises(ValueError):prepare(source,source/'output')


if __name__=='__main__':unittest.main()
