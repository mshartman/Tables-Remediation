import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import layout_compare as lab
from corpus_evaluate import selected_records,tokens,review_flags,evaluate,block_network,semantic_text


class EvaluationTests(unittest.TestCase):
    def test_inline_chemistry_is_not_split_by_measurement(self):
        from bs4 import BeautifulSoup
        soup=BeautifulSoup('<main><p>BiVO<sub>4</sub> and VO<sub>x</sub>.</p><p>Another <em>paragraph</em>.</p>'
                           '<table><tr><td>1</td><td>23</td></tr></table></main>','html.parser')
        result=tokens(semantic_text(soup.main))
        self.assertEqual(result['bivo4'],1);self.assertEqual(result['vox'],1)
        self.assertEqual(result['1'],1);self.assertEqual(result['23'],1);self.assertEqual(result['123'],0)

    def manifest(self,source,data):
        sha=hashlib.sha256(data).hexdigest()
        return {'source_directory':str(source),'records':[
            {'filename':'sample.pdf','sha256':sha,'bytes':len(data),'split':'development','previously_reviewed':False},
            {'filename':'never-open.pdf','sha256':'f'*64,'bytes':100,'split':'holdout','previously_reviewed':False}],
            'development_sample_sha256':[sha]}

    def test_holdout_and_duplicate_leakage_are_rejected(self):
        m=self.manifest(Path('.'),b'source')
        self.assertEqual(len(selected_records(m)),1)
        m['development_sample_sha256']=['f'*64]
        with self.assertRaises(ValueError):selected_records(m)
        m=self.manifest(Path('.'),b'source')
        m['records'].append(dict(m['records'][0],split='holdout'))
        with self.assertRaises(ValueError):selected_records(m)

    def test_coverage_is_normalized_but_not_ground_truth(self):
        self.assertEqual(tokens('ligature ﬁnding'),tokens('ligature finding'))
        self.assertEqual(tokens('conver-\nsion'),tokens('conversion'))
        result={'status':'converted','profile':{'pages':4,'table_caption_pages':[2]},'metrics':{
            'rendered_pages':4,'table_findings':[],'tables':0,'fallback_pages':[],
            'headings':[],'matched_source_token_fraction':.7,'static_findings':[]}}
        self.assertIn('table_captions_without_tables',review_flags(result))
        self.assertIn('text_preservation_review',review_flags(result))

    def test_network_block_is_enforced(self):
        import socket
        with patch('socket.create_connection'),patch('socket.socket.connect'),patch('socket.socket.connect_ex'):
            block_network()
            with self.assertRaises(RuntimeError):socket.create_connection(('example.com',443))

    def test_offline_worker_copies_verified_source_and_preserves_baseline(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);source=root/'source';source.mkdir();pdf=source/'sample.pdf'
            with lab.pymupdf.open() as doc:
                page=doc.new_page();page.insert_text((70,100),'A local baseline article',fontsize=18)
                page.insert_text((70,140),'Readable source content remains available for comparison.')
                doc.save(pdf)
            before=pdf.read_bytes();manifest=root/'manifest.json'
            manifest.write_text(json.dumps(self.manifest(source,before)),encoding='utf-8')
            run=evaluate(manifest,root/'baseline',timeout=30,workers=1)
            self.assertEqual(run['summary']['statuses'],{'converted':1})
            self.assertEqual(run['summary']['holdout_documents_evaluated'],0)
            self.assertEqual(run['summary']['manual_reviews_completed'],0)
            self.assertEqual(pdf.read_bytes(),before)
            case=root/'baseline'/'cases'/run['results'][0]['case_id']
            self.assertEqual((case/'original.pdf').read_bytes(),before)
            self.assertTrue((root/'baseline'/'index.html').exists())
            for name,digest in run['code_sha256'].items():
                self.assertEqual(hashlib.sha256((root/'baseline'/'code-snapshot'/name).read_bytes()).hexdigest(),digest)
            with self.assertRaises(ValueError):evaluate(manifest,root/'baseline')


if __name__=='__main__':unittest.main()
