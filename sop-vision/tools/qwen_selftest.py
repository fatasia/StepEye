"""Offline tests for the optional native Qwen setup tools."""
import hashlib
from pathlib import Path
import tempfile
import subprocess
import unittest
from unittest.mock import patch
import zipfile

from qwen_download import digest,fetch,extract_runtime


class SetupTests(unittest.TestCase):
    def test_existing_verified_asset_skips_network(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d); data=b'local fixture'; (root/'model.gguf').write_bytes(data)
            item={'name':'model.gguf','bytes':len(data),'sha256':hashlib.sha256(data).hexdigest()}
            with patch('qwen_download.subprocess.run',side_effect=AssertionError('no download')):
                self.assertEqual(fetch(item,root,15)['status'],'verified')

    def test_existing_mismatch_not_overwritten(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d); (root/'model.gguf').write_bytes(b'keep')
            item={'name':'model.gguf','bytes':1,'sha256':'0'*64}
            self.assertEqual(fetch(item,root,15)['status'],'existing_file_mismatch')
            self.assertEqual((root/'model.gguf').read_bytes(),b'keep')

    def test_download_failures_return_status(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)
            item={'name':'model.gguf','bytes':10,'sha256':'0'*64,'url':'https://example.invalid/model'}
            for error in (OSError('fixture'),subprocess.TimeoutExpired('curl',1)):
                with patch('qwen_download.shutil.which',return_value='curl'), patch('qwen_download.subprocess.run',side_effect=error):
                    self.assertEqual(fetch(item,root,15)['status'],'download_failed')

    def test_runtime_archive_traversal_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d); path=root/'runtime.zip'
            with zipfile.ZipFile(path,'w') as z: z.writestr('../escape','x')
            item={'name':'runtime.zip','role':'runtime','sha256':digest(path)}
            with self.assertRaises(ValueError): extract_runtime(root,[item])
            self.assertFalse((root.parent/'escape').exists())

    def test_verified_runtime_extracts(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d); path=root/'runtime.zip'
            with zipfile.ZipFile(path,'w') as z: z.writestr('bin/fixture.txt','x')
            item={'name':'runtime.zip','role':'runtime','sha256':digest(path)}
            self.assertEqual(Path(extract_runtime(root,[item]))/'bin/fixture.txt',root/'runtime/bin/fixture.txt')
            self.assertEqual((root/'runtime/bin/fixture.txt').read_text(),'x')

if __name__=='__main__': unittest.main()
