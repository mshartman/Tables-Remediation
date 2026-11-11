"""Structural safeguards; these do not replace real keyboard testing."""
import tempfile
import unittest
from pathlib import Path

from bs4 import BeautifulSoup
from reader_view import READER_SCRIPT, READER_SCRIPT_HASH, render_reader
import base64
import hashlib
import subprocess
import shutil
import json


class KeyboardTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which('node'), 'Node required for navigation execution test')
    def test_cross_origin_history_and_focus_execution(self):
        harness = r"""
const vm = require('node:vm');
const assert = require('node:assert/strict');
for (const failHistory of [false, true]) {
  const events = {}; let focused = false; let scrolled = false;
  const location = {href:'https://sunysb.hosts.atlas-sys.com/nonshib/AccessibleHtmlViewer.html?job_id=test',hash:''};
  const target = {matches:()=>true,focus:()=>{focused=true;},scrollIntoView:()=>{scrolled=true;}};
  const context = {URL,location,window:{location,addEventListener:(k,v)=>events[k]=v},
    document:{baseURI:'https://example.awsapprunner.com/runs/test/output/',getElementById:()=>target,addEventListener:(k,v)=>events[k]=v},
    history:{pushState:(a,b,url)=>{assert.equal(new URL(url).origin,new URL(location.href).origin);assert.equal(new URL(url).search,'?job_id=test');if(failHistory)throw Error('blocked');}}};
  vm.runInNewContext(SCRIPT,context);
  events.click({target:{closest:()=>({matches:()=>true,getAttribute:()=>'#reader-section-2'})},button:0,preventDefault:()=>{}});
  assert.ok(focused && scrolled);
}
""".replace('SCRIPT', json.dumps(READER_SCRIPT))
        subprocess.run(['node', '-e', harness], check=True, capture_output=True)

    def test_history_ignores_cross_origin_base(self):
        self.assertIn('new URL(window.location.href)', READER_SCRIPT)
        self.assertIn("history.pushState(null, '', destination.href)", READER_SCRIPT)
        self.assertNotIn("history.pushState(null, '', hash)", READER_SCRIPT)
        self.assertIn("'popstate'", READER_SCRIPT)

    def test_targets_and_table_survive_repeated_render(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'article.html'
            path.write_text('<section class="page"><p>First page</p></section>'
                            '<section class="page"><table><caption>Results</caption>'
                            '<tr><th>Measure</th><td>5</td></tr></table></section>',
                            encoding='utf-8')
            for _ in range(2):
                render_reader(path, 'Keyboard example')
                soup = BeautifulSoup(path.read_text(encoding='utf-8'), 'html.parser')
                for link in soup.select('.reader-skip, .reader-nav a'):
                    target = soup.find(id=link['href'][1:])
                    self.assertIsNotNone(target)
                    self.assertEqual(target['tabindex'], '-1')
                self.assertEqual(len(soup.select('.reader-table')), 1)
                self.assertEqual(soup.select_one('.reader-table')['tabindex'], '0')
                self.assertTrue(soup.select_one('nav details').has_attr('open'))
                self.assertFalse(soup.select('[tabindex]:not([tabindex="0"]):not([tabindex="-1"])'))
                self.assertEqual(soup.select_one('.page')['aria-label'], 'Page 1 of 2')
                self.assertEqual(len(soup.select('script')), 1)
                self.assertEqual(soup.script.string, READER_SCRIPT)
                self.assertEqual(base64.b64encode(hashlib.sha256(soup.script.string.encode()).digest()).decode(), READER_SCRIPT_HASH)


if __name__ == '__main__':
    unittest.main()
