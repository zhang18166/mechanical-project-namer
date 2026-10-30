# SPDX-License-Identifier: GPL-3.0-only
"""Visible Windows GUI regression: missing formal directory and cancel queue."""
import argparse
import json
import os
import time
from pathlib import Path
from scripts.publish_harness import Fixture,write
from mechanical_project_namer.publish_protocol import data
from mechanical_project_namer.publish_ledger import PublishLedger
from PyQt6.QtWidgets import QApplication
from PyQt6.QtTest import QTest
from mechanical_project_namer.ui.publish_dialog import PublishDialog


def run(root):
    app=QApplication([]);f=Fixture(root,large=True);s=f.service();c=s.start();d=PublishDialog(f.snap,f.v1)
    d.config.setText(str(s.root/'publisher.json'));d.show();QTest.qWait(100)
    def wait():
        end=time.monotonic()+40
        while d.worker:
            assert time.monotonic()<end,'window stalled'
            QTest.qWait(20)
    try:
        d.publish();wait();assert '已提交' in d.status.text();v1=d.remote
        missing=s.root/'storage/versions'/v1
        os.rename(missing,f.root/'only-test-missing-formal-version')
        assert (s.root/'storage/tasks'/v1/'0').is_file()
        assert not c.versions()[0]['healthy']
        d.query();wait();assert '不可用' in d.status.text()
        d.grab().save(str(f.root/'gui-unavailable.png'))
        d.publish();wait();assert '不可用' in d.status.text() and '失败' in d.status.text()
        d.target.setText(str(f.root/'must-not-download'));d.download();wait();assert '失败' in d.status.text()
        assert not (f.root/'must-not-download').exists()
        # A new V2 remains independently publishable. Stop and cancel while a
        # real receiving socket is occupied; cancellation queues behind it.
        d.task=f.v2;d.config_changed();s.set_control(trace_chunks=True,delay_per_chunk=.03)
        d.publish()
        end=time.monotonic()+15
        while not any(r['event']=='upload-bytes' and r['file']==0 for r in s.records()):
            assert time.monotonic()<end;QTest.qWait(10)
        started=time.monotonic();d.cancel_remote();wait()
        # finished schedules a queued explicit server cancellation.
        QTest.qWait(50);wait()
        elapsed=time.monotonic()-started
        row=f.snap._get(f.v2);ledger=PublishLedger(f.snap);key=ledger.key(c.config,f.snap.store_id,row['version_id'])
        old=ledger.get(key);assert old['state']=='cancelled',old
        assert c.request('GET','/tasks/'+old['task'])['state']=='cancelled'
        assert elapsed<12,elapsed
        s.set_control();d.publish(restart=True);wait();assert '已提交' in d.status.text()
        assert d.remote!=old['task']
        d.grab().save(str(f.root/'gui-reissued.png'))
        # Finish/then-cancel must never delete a committed version.
        d.cancel_remote();wait();assert '已提交' in d.status.text()
        f.assert_protected()
        result=dict(passed=True,formal_missing_unavailable_everywhere=True,no_staging_repair=True,
                    cancel_queued_seconds=elapsed,cancelled_task=old['task'],new_task=d.remote,finished_cancel_preserved=True)
        (f.root/'report.json').write_bytes(data(result));print(data(result).decode())
    finally:
        d.close();QTest.qWait(30);f.close()


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True);a=p.parse_args();run(a.output)
