# SPDX-License-Identifier: GPL-3.0-only
"""Historical work locations remain metadata, never an automatic recovery source."""
from .snapshot_io import SnapshotError

DETACHED = '工作文件待重新关联'


def restored_instance(database):
    with database.connect() as con:
        return con.execute("SELECT 1 FROM snapshot_meta WHERE key='restored_instance'").fetchone() is not None


def reject_detached_task(database, key, *, restore=False):
    prefix = 'detached_restore:' if restore else 'detached_task:'
    with database.connect() as con:
        if con.execute('SELECT 1 FROM snapshot_meta WHERE key=?', (prefix + key,)).fetchone():
            raise SnapshotError('这是整体恢复前的历史终结任务，仅保留记录；请在新位置创建新任务。')
