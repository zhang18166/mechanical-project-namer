# SPDX-License-Identifier: GPL-3.0-only
"""Same native account GUI acceptance, through the new real Linux HTTPS chain."""
import argparse
import json
import sys
from pathlib import Path
from scripts import verify_accounts_gui
from scripts.https_harness import fixture
from mechanical_project_namer.publish_protocol import data

def native_application(arguments):
    from PyQt6.QtWidgets import QApplication
    app=QApplication(arguments)
    assert app.platformName()=='windows','Real native Windows platform required'
    return app

def no_preview_engines():
    assert not any(name.startswith(('PyQt6.QtWebEngine','PyQt6.QtPdf','OCP','win32com')) for name in sys.modules)
    return True

def negative(root):
    import os,time
    from PyQt6.QtCore import Qt
    from PyQt6.QtTest import QTest
    from scripts.make_tls_fixtures import generate
    from mechanical_project_namer.ui.project_files_page import ProjectFilesPage
    app=native_application([]);f=fixture(root)
    os.environ['MECHANICAL_PROJECT_NAMER_DEV_ROOT']=str(f.a.root/'client')
    page=ProjectFilesPage(f.a.repo);page.show()
    QTest.mouseClick(page.account_button,Qt.MouseButton.LeftButton);d=page.account_window
    try:
        ca=generate(f.root/'not-trusted')
        config=dict(f.service.connection,ca=str(ca/'ca.pem'))
        (f.root/'wrong-trust.json').write_bytes(data(config))
        d.connection.setText(str(f.root/'wrong-trust.json'));d.username.setText('fake-admin');d.password.setText(f.service.current)
        QTest.mouseClick(d.login_button,Qt.MouseButton.LeftButton)
        end=time.monotonic()+15
        while d.worker:
            assert time.monotonic()<end;QTest.qWait(20)
        assert 'tls-certificate-rejected' in d.status.text(),d.status.text()
        assert not d.session or not d.session.token
        d.grab().save(str(f.root/'gui-tls-rejected.png'));f.protect()
        (f.root/'report.json').write_bytes(data(dict(passed=True,qt_platform=app.platformName(),tls_rejection_visible=True,no_session=True,
                                                   preview_and_cad_engines_not_loaded=no_preview_engines())))
        print('Native GUI rejected untrusted TLS certificate')
    finally:d.close();page.close();QTest.qWait(40);f.close()

def confirmation(root):
    import os,time
    from PyQt6.QtCore import Qt
    from PyQt6.QtTest import QTest
    from scripts.verify_https_transfers import restart_after_fault
    from mechanical_project_namer.ui.project_files_page import ProjectFilesPage
    app=native_application([]);f=fixture(root);f.admin.publisher(f.pa).publish(f.a.snap,f.task)
    os.environ['MECHANICAL_PROJECT_NAMER_DEV_ROOT']=str(f.a.root/'client')
    page=ProjectFilesPage(f.a.repo);page.resize(1100,850);page.show()
    QTest.mouseClick(page.account_button,Qt.MouseButton.LeftButton);d=page.account_window
    def wait(failed=False):
        end=time.monotonic()+40
        while d.worker:
            assert time.monotonic()<end;QTest.qWait(20)
        assert ('操作未完成' in d.status.text())==failed,d.status.text()
    try:
        d.connection.setText(str(f.service.root/'connection.json'));d.username.setText('fake-reader');d.password.setText(f.users['fake-reader']['password'])
        QTest.mouseClick(d.login_button,Qt.MouseButton.LeftButton);wait()
        QTest.mouseClick(d.refresh_button,Qt.MouseButton.LeftButton);wait()
        d.projects.setCurrentRow(next(i for i,p in enumerate(d.project_rows) if p['id']==f.pa));wait()
        assert d.versions.count()==1 and '上次查询' in d.versions.item(0).text()
        assert '本次操作已确认' in d.confirmation_label.text() and f.service.connection['service'] in d.confirmation_label.text()
        stamp=d.last_confirmed_at
        f.service.stop()  # Only this fixture's two owned processes; no system network changes.
        d.load_versions(d.projects.currentRow());wait(True)
        assert '当前未确认' in d.confirmation_label.text() and d.last_confirmed_at==stamp and d.versions.count()==1
        d.grab().save(str(f.root/'gui-history-offline.png'))
        restart_after_fault(f.service)
        d.load_versions(d.projects.currentRow());wait()
        assert '本次操作已确认' in d.confirmation_label.text() and d.last_confirmed_at!=stamp
        d.grab().save(str(f.root/'gui-history-reverified.png'));f.protect()
        (f.root/'report.json').write_bytes(data(dict(passed=True,qt_platform=app.platformName(),target_identity_visible=True,
          historical_list_retained=True,offline_not_current=True,reverified=True,preview_and_cad_engines_not_loaded=no_preview_engines())))
        print('Native GUI target/history/current confirmation verified through real stop and restart')
    finally:d.close();page.close();QTest.qWait(40);f.close()

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True);p.add_argument('--negative',action='store_true');p.add_argument('--confirmation',action='store_true');a=p.parse_args()
    verify_accounts_gui.AuthFixture=fixture
    verify_accounts_gui.QApplication=native_application
    if a.confirmation:confirmation(a.output)
    elif a.negative:negative(a.output)
    else:
        verify_accounts_gui.run(a.output,'linux')
        report=json.loads((a.output/'report.json').read_bytes());report['qt_platform']='windows'
        report['preview_and_cad_engines_not_loaded']=no_preview_engines()
        (a.output/'report.json').write_bytes(data(report))
