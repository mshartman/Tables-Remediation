"""Public-clone smoke check without touching the owner's private renderer."""
import os
from pathlib import Path
import subprocess
import sys
import unittest


class PublicDistributionTests(unittest.TestCase):
    def test_apps_and_legacy_fallback_work_when_private_renderer_is_absent(self):
        code = r'''
import importlib.abc
import json
from pathlib import Path
import socket
import sys
import tempfile

class WithoutPrivateRenderer(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == 'experimental_layout.iris_layout':
            raise ModuleNotFoundError('Private renderer omitted', name=fullname)

def forbidden(*args, **kwargs):
    raise AssertionError('Network calls forbidden in public smoke test')

sys.dont_write_bytecode = True
sys.meta_path.insert(0, WithoutPrivateRenderer())
socket.create_connection = forbidden
import layout_compare
assert layout_compare.iris_layout is None
import local_server
assert local_server.app.test_client().get('/health').status_code == 200
import reader_local
assert reader_local.app.test_client().get('/').status_code == 200
import layout_pipeline
with tempfile.TemporaryDirectory() as folder:
    root = Path(folder)
    legacy = root/'legacy.html'
    legacy.write_text('<html><body>Legacy result</body></html>', encoding='utf-8')
    direct = root/'must-not-be-created'
    try:
        layout_compare.compare(root/'unused.pdf', direct, geometry_only=True)
    except RuntimeError as error:
        assert 'private' in str(error)
    else:
        raise AssertionError('Missing private renderer must produce a clear error')
    assert not direct.exists()
    assert layout_pipeline.build_layout_preview(root/'unused.pdf', root, legacy, {}, 'Test') is None
    assert json.loads((root/'layout-status.json').read_text())['status'] == 'fallback'
    assert legacy.exists()
print('Public apps and fallback verified without the private renderer.')
'''
        env = dict(os.environ, RESULTS_S3_ENABLED='false', AWS_EC2_METADATA_DISABLED='true',
                   CAMPUS_VISION_ENABLED='false', OLLAMA_ENABLED='false', READER_LAYOUT_MODE='geometry')
        result = subprocess.run([sys.executable, '-c', code], cwd=Path(__file__).resolve().parent,
                                env=env, capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('Public apps and fallback verified', result.stdout)


if __name__ == '__main__':
    unittest.main()
