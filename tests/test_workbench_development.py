# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 zhang18166

import os
import tempfile
from pathlib import Path

import pytest

from mechanical_project_namer.development import (
    DEV_ROOT_ENV, MARKER, configure_development, is_development, seed_demo,
)
from mechanical_project_namer.paths import database_path, exports_dir, logs_dir, settings_dir, ai_memory_dir


@pytest.fixture
def profile(tmp_path, monkeypatch):
    # configure_development deliberately modifies only this process's environment.
    for name in (DEV_ROOT_ENV, 'LOCALAPPDATA', 'APPDATA', 'TEMP', 'TMP',
                 'MECHANICAL_PROJECT_NAMER_INSTANCE_NAME', 'MECHANICAL_PROJECT_NAMER_SMOKE_CHECKS',
                 'MECHANICAL_PROJECT_NAMER_SMOKE_QUIT_MS'):
        if name in os.environ:
            monkeypatch.setenv(name, os.environ[name])
        else:
            monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(tempfile, 'tempdir', None)
    return tmp_path / 'development'


def test_default_entry_does_not_change_environment_or_paths(profile):
    before = dict(os.environ)
    paths = database_path(), settings_dir(), exports_dir(), logs_dir()
    assert configure_development([]) == ([], False)
    assert dict(os.environ) == before
    assert (database_path(), settings_dir(), exports_dir(), logs_dir()) == paths
    assert not is_development() and not profile.exists()


def test_profile_isolates_every_store_temp_and_instance_and_reuses_identity(profile, monkeypatch):
    production_local, production_roaming = os.environ['LOCALAPPDATA'], os.environ['APPDATA']
    monkeypatch.setenv('MECHANICAL_PROJECT_NAMER_INSTANCE_NAME', 'MechanicalProjectNamer.V11.SingleInstance')
    monkeypatch.setenv('MECHANICAL_PROJECT_NAMER_SMOKE_CHECKS', '1')
    assert configure_development(['--dev', '--dev-root', str(profile), '--dev-smoke']) == ([], True)
    for path in (database_path(), settings_dir(), exports_dir(), logs_dir(), ai_memory_dir(), Path(tempfile.gettempdir())):
        assert path.is_relative_to(profile)
    instance = os.environ['MECHANICAL_PROJECT_NAMER_INSTANCE_NAME']
    assert instance.startswith('MechanicalWorkbench.Dev.')
    assert 'MECHANICAL_PROJECT_NAMER_SMOKE_CHECKS' not in os.environ
    # Start the same profile again from its original parent environment.
    monkeypatch.setenv('LOCALAPPDATA', production_local)
    monkeypatch.setenv('APPDATA', production_roaming)
    configure_development(['--dev', '--dev-root', str(profile)])
    assert os.environ['MECHANICAL_PROJECT_NAMER_INSTANCE_NAME'] == instance


@pytest.mark.parametrize('target', ['local', 'roaming', 'ancestor', 'repository', 'unknown', 'bad-marker'])
def test_unsafe_profile_is_rejected_before_environment_changes(profile, target):
    local, roaming = Path(os.environ['LOCALAPPDATA']), Path(os.environ['APPDATA'])
    choices = dict(local=local/'MechanicalProjectNamer'/'dev', roaming=roaming/'MechanicalProjectNamer',
                   ancestor=local, repository=Path(__file__).resolve().parents[1]/'dev-data',
                   unknown=profile, **{'bad-marker': profile})
    root = choices[target]
    if target in {'unknown', 'bad-marker'}:
        root.mkdir()
        (root/('user-file.txt' if target == 'unknown' else MARKER)).write_text('user content')
    before = dict(os.environ)
    with pytest.raises(ValueError):
        configure_development(['--dev', '--dev-root', str(root)])
    assert dict(os.environ) == before


def test_nested_junction_in_existing_profile_is_rejected(profile, monkeypatch):
    local, roaming = os.environ['LOCALAPPDATA'], os.environ['APPDATA']
    configure_development(['--dev', '--dev-root', str(profile)])
    suspect = profile/'local'/'MechanicalProjectNamer'
    suspect.mkdir()
    monkeypatch.setenv('LOCALAPPDATA', local); monkeypatch.setenv('APPDATA', roaming)
    actual = Path.is_junction
    monkeypatch.setattr(Path, 'is_junction', lambda p: p == suspect or actual(p))
    with pytest.raises(ValueError, match='链接'):
        configure_development(['--dev', '--dev-root', str(profile)])


def test_credentials_ai_and_sw_are_blocked_before_external_access(profile, qtbot):
    from mechanical_project_namer.security.credential_store import CredentialStore, CredentialStoreError
    from mechanical_project_namer.ai.client import AIClient, AIClientError
    from mechanical_project_namer.solidworks.connection import connect_active_application
    from mechanical_project_namer.solidworks.controller import SolidWorksWorkerController
    from mechanical_project_namer.solidworks.worker_entry import run_worker
    from mechanical_project_namer.solidworks.errors import SolidWorksError
    from mechanical_project_namer.solidworks.models import OperationRequest
    configure_development(['--dev', '--dev-root', str(profile)])
    credentials = CredentialStore()
    assert credentials.backend is None and credentials.get_api_key() is None
    with pytest.raises(CredentialStoreError): credentials.set_api_key('fictional-test-value')
    with pytest.raises(CredentialStoreError): credentials.delete_api_key()
    with pytest.raises(AIClientError) as error:
        AIClient('https://invalid.example', 'demo', 'fictional').discover_models()
    assert error.value.code == 'development_disabled'
    controller = SolidWorksWorkerController()
    with pytest.raises(SolidWorksError): controller.start(OperationRequest('demo'), 1)
    assert controller.process is None and controller.request_path is None
    with pytest.raises(SolidWorksError): connect_active_application()
    with pytest.raises(SolidWorksError): run_worker('does-not-exist.json')


def test_development_instance_notifies_only_its_own_server(profile, qtbot):
    from uuid import uuid4
    from mechanical_project_namer.single_instance import SingleInstance
    configure_development(['--dev', '--dev-root', str(profile)])
    name = os.environ['MECHANICAL_PROJECT_NAMER_INSTANCE_NAME']
    assert name != 'MechanicalProjectNamer.V11.SingleInstance'
    # A synthetic legacy server exercises notification routing without claiming
    # the installed application's actual mutex or named pipe.
    legacy = SingleInstance('MechanicalWorkbench.Test.Legacy.' + uuid4().hex)
    development = SingleInstance(name)
    duplicate = SingleInstance(name)
    notifications, legacy_notifications = [], []
    development.show_requested.connect(lambda: notifications.append(True))
    legacy.show_requested.connect(lambda: legacy_notifications.append(True))
    try:
        assert legacy.start_or_notify()
        assert development.start_or_notify()
        assert not duplicate.start_or_notify()
        qtbot.waitUntil(lambda: len(notifications) == 1)
        assert legacy_notifications == []
    finally:
        development.release()
        legacy.release()


def test_demo_restart_does_not_duplicate_records_or_advance_number(profile):
    from mechanical_project_namer.data import Database, Repository, DataExporter
    from mechanical_project_namer.numbering.workflow import NamingWorkflow
    from mechanical_project_namer.numbering.simple import SIMPLE_TYPE
    from mechanical_project_namer.settings import AppSettings, save_settings, load_settings
    configure_development(['--dev', '--dev-root', str(profile)])
    database = Database(); database.migrate()
    repo = Repository(database); exporter = DataExporter(repo)
    workflow = NamingWorkflow(repo, exporter); settings = AppSettings()
    seed_demo(repo, workflow, settings)
    project = repo.list_projects()[0]
    assert project.code == 'DEMO' and Path(project.directory).is_relative_to(profile/'workspace')
    assert len(repo.list_records()) == 1
    assert repo.get_next_named_number(project.id, 'DJ', SIMPLE_TYPE) == 2
    save_settings(settings)
    seed_demo(repo, workflow, load_settings())
    assert len(repo.list_records()) == 1 and len(repo.list_projects()) == 1
    assert repo.get_next_named_number(project.id, 'DJ', SIMPLE_TYPE) == 2
    assert not settings.solidworks_enabled
    assert list(exports_dir().glob('*.json'))


@pytest.mark.parametrize('arguments', [['--dev-root', 'unused'], ['--dev-smoke']])
def test_development_options_require_explicit_dev(arguments):
    with pytest.raises(SystemExit) as error:
        configure_development(arguments)
    assert error.value.code == 2
