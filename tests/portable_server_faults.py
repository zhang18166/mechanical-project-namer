# SPDX-License-Identifier: GPL-3.0-only
"""Linux native process, link and permission tests. Explicit run, no skipped Windows cases."""
import os
from pathlib import Path
import tempfile,unittest,uuid
from server_fault_cases import BACKUP_POINTS,RESTORE_POINTS,APPROVE_POINTS,hard_exit_case,approval_exit_case
from server_fixture import make_service
from mechanical_project_namer.server_backups import ServerBackups


class LinuxServerFaults(unittest.TestCase):
    def setUp(self):
        self.assertEqual(os.name,'posix');self.assertNotEqual(os.geteuid(),0)
        self.temp=tempfile.TemporaryDirectory(prefix='mw-server-native-');self.root=Path(self.temp.name)
    def tearDown(self):self.temp.cleanup()
    def test_linked_source_rejected(self):
        f=make_service(self.root/'source');payload=f['store'].root/'versions'/f['task']/'0'
        moved=self.root/'original';payload.rename(moved);payload.symlink_to(moved)
        with self.assertRaises(ValueError):ServerBackups().create(f['config_path'],self.root/'backup',task_id=str(uuid.uuid4()))
        self.assertTrue(moved.is_file());self.assertFalse((self.root/'backup').exists())
    def test_linked_target_rejected(self):
        f=make_service(self.root/'source');real=self.root/'real';real.mkdir();(self.root/'link').symlink_to(real,target_is_directory=True)
        with self.assertRaises(ValueError):ServerBackups().create(f['config_path'],self.root/'link/backup',task_id=str(uuid.uuid4()))
        self.assertEqual(list(real.iterdir()),[])
    def test_actual_unwritable_destination(self):
        f=make_service(self.root/'source');folder=self.root/'denied';folder.mkdir();folder.chmod(0o500)
        try:
            with self.assertRaises(PermissionError):ServerBackups().create(f['config_path'],folder/'backup',task_id=str(uuid.uuid4()))
            self.assertFalse((folder/'backup').exists())
        finally:folder.chmod(0o700)
    def test_same_logical_service_two_locations_refused(self):
        from server_http_cases import duplicate_logical_instance_case
        duplicate_logical_instance_case(self.root/'case')


def case(action,point):
    def test(self):
        if action=='approve':approval_exit_case(self.root,point)
        else:hard_exit_case(self.root,action,point)
    return test


for action,points in [('backup',BACKUP_POINTS),('restore',RESTORE_POINTS),('approve',APPROVE_POINTS)]:
    for point in points:setattr(LinuxServerFaults,'test_hard_exit_'+point.replace('-','_'),case(action,point))

if __name__=='__main__':unittest.main()
