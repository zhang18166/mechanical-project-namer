# SPDX-License-Identifier: GPL-3.0-only
"""Native Windows GUI -> independent account service; screenshots remain private."""
import argparse,json,os,secrets,time
from pathlib import Path
from PyQt6.QtWidgets import QApplication,QPushButton
from PyQt6.QtTest import QTest
from PyQt6.QtCore import Qt
from scripts.auth_harness import AuthFixture,rejected
from mechanical_project_namer.account_client import AccountSession
from mechanical_project_namer.publish_protocol import data,digest
from mechanical_project_namer.ui.project_files_page import ProjectFilesPage


def run(root,platform):
    app=QApplication([]);f=AuthFixture(root,platform,large=True)
    os.environ['MECHANICAL_PROJECT_NAMER_DEV_ROOT']=str(f.a.root/'client')
    page=ProjectFilesPage(f.a.repo);page.resize(1100,800);page.show();QTest.qWait(100)
    assert page.account_button.isVisible()
    QTest.mouseClick(page.account_button,Qt.MouseButton.LeftButton);QTest.qWait(50)
    d=page.account_window;assert d and d.isVisible()
    d.snapshots=f.a.snap;d.local_changed()
    d.connection.setText(str(f.service.root/'connection.json'));d.show();QTest.qWait(100)
    def click(button):
        assert button.isEnabled(),button.text()
        QTest.mouseClick(button,Qt.MouseButton.LeftButton)
    def wait():
        end=time.monotonic()+60
        while d.worker:
            assert time.monotonic()<end,'GUI timeout'
            QTest.qWait(20)
        assert '操作未完成' not in d.status.text(),d.status.text()
    def login(name,pw):
        d.username.setText(name);d.password.setText(pw);click(d.login_button);wait()
    def project():
        click(d.refresh_button);wait()
        i=next(i for i,p in enumerate(d.project_rows) if p['id']==f.pa)
        d.projects.setCurrentRow(i);wait()
    started=time.monotonic()
    try:
        login('fake-admin',f.service.current);assert d.admin.isVisible()
        temporary=secrets.token_urlsafe(24);changed=secrets.token_urlsafe(24)
        d.create_name.setText('fake-gui-member');d.role.setCurrentIndex(d.role.findData('designer'));d.initial_password.setText(temporary)
        click(d.create_button);wait();key=d.target_user.text();assert key
        project()
        for value in d.permissions.values():value.setChecked(True)
        click(d.grant_button);wait()
        d.grab().save(str(f.root/'gui-admin-grants.png'))
        click(d.logout_button);wait();assert not d.admin.isVisible()
        login('fake-gui-member',temporary);assert not d.admin.isVisible() and d.session.user['must_change']
        d.new_password.setText(changed);click(d.change_button);wait();assert not d.session.user
        login('fake-gui-member',changed);assert not d.admin.isVisible()
        project();d.local_version.setCurrentIndex(d.local_version.findData(f.task))
        click(d.publish_button);wait()
        published=json.loads(d.details.toPlainText());assert published['state']=='complete' and published['healthy'] and published['publisher']['id']==key
        project();d.versions.setCurrentRow(0);d.download_target.setText(str(f.root/'gui-download'))
        click(d.download_button);wait()
        for entry in published['manifest']['files']:
            assert digest(f.root/'gui-download'/entry['path'])=={k:entry[k] for k in ('size','sha256')}
        d.grab().save(str(f.root/'gui-member-published.png'))
        alive=AccountSession(f.service.connection);alive.login('fake-gui-member',changed)
        rejected(lambda:alive.request('POST','/admin/users',dict(username='fake-forged',role='admin',initial_password=temporary)))
        click(d.logout_button);wait()
        login('fake-admin',f.service.current);d.target_user.setText(key)
        click(d.disable_button);wait();rejected(lambda:alive.me())
        reset=secrets.token_urlsafe(24);d.initial_password.setText(reset);click(d.reset_button);wait()
        rows=d.session.request('GET','/admin/users')['users'];row=next(r for r in rows if r['id']==key)
        assert not row['enabled'] and row['must_change']
        rejected(lambda:AccountSession(f.service.connection).login('fake-gui-member',reset))
        click(d.audit_button);wait();assert 'grants-replaced' in d.details.toPlainText()
        d.grab().save(str(f.root/'gui-admin-audit.png'))
        click(d.logout_button);wait();assert not d.admin.isVisible() and not d.session.token
        f.protect()
        result=dict(passed=True,real_windows_gui=True,project_files_entry=True,service_platform=platform,admin_create_disable_reset_grant=True,first_password_change=True,
                    ordinary_admin_hidden=True,backend_rejected=True,published_task=published['task'],download_hashes=True,logout=True,
                    seconds=time.monotonic()-started,source_protected=True,environment=f.service.info())
        (f.root/'report.json').write_bytes(data(result));print(data(result).decode())
    finally:d.close();page.close();QTest.qWait(40);f.close()


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True);p.add_argument('--platform',choices=['windows','linux'],default='linux');a=p.parse_args();run(a.output,a.platform)
