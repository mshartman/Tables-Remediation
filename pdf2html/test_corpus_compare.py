import copy
import unittest
from corpus_compare import compare_runs


class ComparisonTests(unittest.TestCase):
    def baseline(self):
        return {'status':'complete','manifest_sha256':'same','limits':{'max_pages':40},'results':[
            {'sha256':'source','case_id':'case','filename':'sample.pdf','status':'converted',
             'metrics':{'matched_source_token_fraction':.5,'headings':[],'tables':0,'rendered_pages':2,'static_findings':[]}}]}

    def test_changed_manifest_or_unfinished_run_is_rejected(self):
        old=self.baseline();new=self.baseline();new['status']='running'
        with self.assertRaises(ValueError):compare_runs(old,new)
        new=self.baseline();new['measurement_version']=2
        with self.assertRaises(ValueError):compare_runs(old,new)
        new=self.baseline();new['manifest_sha256']='different'
        with self.assertRaises(ValueError):compare_runs(old,new)

    def test_improvements_are_not_called_accuracy(self):
        old=self.baseline();new=copy.deepcopy(old);new['results'][0]['metrics']['matched_source_token_fraction']=.95
        result=compare_runs(old,new)
        self.assertEqual(result['summary']['cases_with_coverage_increase_over_10_points'],1)
        self.assertEqual(result['summary']['human_reviews_completed'],0)
        self.assertEqual(result['cases'][0]['manual_review'],'unreviewed')

    def test_regression_and_changed_headings_are_exposed(self):
        old=self.baseline();new=copy.deepcopy(old);new['results'][0]['metrics'].update(
            matched_source_token_fraction=.1,headings=[{'level':'h2','text':'New heading'}])
        result=compare_runs(old,new)
        self.assertTrue(result['cases'][0]['headings_changed'])
        self.assertEqual(result['summary']['cases_flagged_for_regression_review'],1)


if __name__=='__main__':unittest.main()
