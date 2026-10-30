# SPDX-License-Identifier: GPL-3.0-only
"""Linux-only real filesystem checks; executed explicitly, never faked on Windows."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from mechanical_project_namer.publish_protocol import safe,manifest,ProtocolError
from mechanical_project_namer.library_lock import file_lease
from mechanical_project_namer.account_server import read_password


class LinuxFilesystem(unittest.TestCase):
    def setUp(self):
        self.assertEqual(os.name,'posix');self.assertNotEqual(os.geteuid(),0)
        self.temp=tempfile.TemporaryDirectory(prefix='workbench-linux-fs-');self.root=Path(self.temp.name)
    def tearDown(self):self.temp.cleanup()

    def test_native_case_and_protocol_conflict(self):
        a=self.root/'Part.prt';b=self.root/'part.prt';a.write_bytes(b'A');b.write_bytes(b'B')
        self.assertNotEqual(a.read_bytes(),b.read_bytes())
        import uuid,hashlib
        m=dict(store=str(uuid.uuid4()),local_project=1,version=str(uuid.uuid4()),label='V1',files=[dict(path=n,role='source',size=1,sha256=hashlib.sha256(b'A').hexdigest()) for n in ('Part.prt','part.prt')])
        with self.assertRaises(ProtocolError):manifest(m)

    def test_symlink_and_parent_escape(self):
        source=self.root/'source';source.mkdir();(source/'dummy.prt').write_bytes(b'FICTIONAL')
        (self.root/'link').symlink_to(source,target_is_directory=True)
        with self.assertRaises(ProtocolError):safe(self.root/'link'/'dummy.prt')

    def test_secret_mode(self):
        p=self.root/'test-password';p.write_text('FICTIONAL PASSWORD ONLY');p.chmod(0o600)
        self.assertEqual(read_password(p),'FICTIONAL PASSWORD ONLY')
        p.chmod(0o644)
        with self.assertRaises(ProtocolError):read_password(p)

    def test_real_interprocess_lock(self):
        path=self.root/'lease'
        code="from mechanical_project_namer.library_lock import file_lease,MaintenanceBusy; import sys;"+ "\ntry:\n with file_lease(sys.argv[1],exclusive=True):sys.exit(3)\nexcept MaintenanceBusy:sys.exit(0)"
        with file_lease(path):
            r=subprocess.run([sys.executable,'-c',code,str(path)],capture_output=True,timeout=10)
            self.assertEqual(r.returncode,0,r.stderr)
        with file_lease(path,exclusive=True):pass

    def test_unreadable_file(self):
        p=self.root/'unreadable';p.write_bytes(b'FICTIONAL');p.chmod(0)
        try:
            with self.assertRaises(PermissionError):p.read_bytes()
        finally:p.chmod(0o600)


if __name__=='__main__':unittest.main()
