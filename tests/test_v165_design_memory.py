# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 zhang18166

from types import SimpleNamespace
import sqlite3

import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtTest import QSignalSpy
from PyQt6.QtWidgets import QInputDialog, QMessageBox

from mechanical_project_namer.ai import NamingMemory
from mechanical_project_namer.ai.design_context import (
    MAX_CONTEXT_CHARS, bound_messages, explicit_preference, summarize_history,
)
from mechanical_project_namer.data import Database, Repository
from mechanical_project_namer.data.database import SCHEMA_VERSION
from mechanical_project_namer.projects import ProjectService
from mechanical_project_namer.ui.design_chat_dialog import DesignChatDialog
from mechanical_project_namer.ui.design_memory_dialog import DesignMemoryDialog
from mechanical_project_namer.ui.shell import ApplicationShell


@pytest.fixture
def setup(tmp_path):
    db = Database(tmp_path / 'design.db')
    db.migrate()
    repo = Repository(db)
    project = ProjectService(repo).create('临时设计项目', '合成测试数据', 'MEMTEST', project_type='test')
    shell = object.__new__(ApplicationShell)
    shell.repository = repo
    shell.memory = NamingMemory(db)
    shell.current_project_id = project.id
    shell.current_module_code = '03'
    shell.chat_conversation_id = repo.create_conversation(project.id, '新对话', 'test', False, 'high')
    shell.design_chat = None
    return db, repo, project, shell


def options(**changes):
    return dict(use_project=True, use_history=True, use_naming=True, use_memory=True,
                use_preferences=True, **changes)


def test_schema8_upgrade_backup_and_idempotence(setup):
    db, repo, project, _ = setup
    with db.transaction() as c:
        c.execute('DROP TABLE design_preferences')
        c.execute('UPDATE schema_meta SET version=7')
    db.migrate()
    backups = list((db.path.parent / 'backups/database').glob('*pre_schema*.db'))
    assert len(backups) == 1
    with sqlite3.connect(backups[0]) as c:
        assert c.execute('SELECT version FROM schema_meta').fetchone()[0] == 7
        assert c.execute('SELECT name FROM projects').fetchone()[0] == project.name
    db.migrate()
    assert len(list((db.path.parent / 'backups/database').glob('*pre_schema*.db'))) == 1
    with db.connect() as c:
        assert c.execute('SELECT version FROM schema_meta').fetchone()[0] == SCHEMA_VERSION
        assert c.execute('PRAGMA foreign_key_check').fetchall() == []


def test_release_metadata_records_target_schema_not_old_production_schema(tmp_path, monkeypatch):
    import json
    import importlib.util
    from pathlib import Path
    spec = importlib.util.spec_from_file_location('design_release_manager', Path(__file__).parents[1] / 'scripts/release_manager.py')
    manager = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(manager)
    (tmp_path / '机械项目命名助手.exe').write_bytes(b'test')
    monkeypatch.setattr(manager, 'guard_clean_worktree', lambda **k: dict(commit='test', tags=['v1.6.5']))
    monkeypatch.setattr(manager, 'schema_version', lambda *a: '7')
    target = manager.write_version(tmp_path, '1.6.4', 283)
    assert json.loads(target.read_text(encoding='utf-8'))['database_schema'] == str(SCHEMA_VERSION)


def test_preference_survives_restart_and_conversation_delete(setup):
    db, repo, _, shell = setup
    mid = repo.add_ai_message(shell.chat_conversation_id, 'user', '记住我的习惯：先给结论，再列步骤。')
    pid = repo.save_design_preference('先给结论，再列步骤。', mid)
    assert repo.save_design_preference('先给结论，再列步骤。') == pid
    reopened = Repository(Database(db.path))
    repo.delete_conversation(shell.chat_conversation_id)
    assert reopened.list_design_preferences()[0]['content'] == '先给结论，再列步骤。'
    assert reopened.list_design_preferences()[0]['source_message_id'] is None


def test_explicit_preference_and_user_message_commit_atomically(setup):
    db, repo, _, shell = setup
    with db.transaction() as c:
        c.execute("CREATE TRIGGER block_preference BEFORE INSERT ON design_preferences BEGIN SELECT RAISE(ABORT, 'test failure'); END")
    with pytest.raises(sqlite3.IntegrityError):
        repo.add_ai_message(shell.chat_conversation_id, 'user', '记住我的习惯：先给结论', design_preference='先给结论')
    assert repo.list_ai_messages(shell.chat_conversation_id) == []


@pytest.mark.parametrize('content', ['', ' ', '字' * 2001])
def test_invalid_preference_not_written(setup, content):
    _, repo, _, _ = setup
    with pytest.raises(ValueError):
        repo.save_design_preference(content)
    assert repo.list_design_preferences() == []


@pytest.mark.parametrize('text,expected', [
    ('记住我的习惯：先问载荷，再比较方案。', '先问载荷，再比较方案。'),
    ('请记住我的偏好: 回答简洁，计算列公式。', '回答简洁，计算列公式。'),
    ('本项目采用40Cr，轴径30mm。', None),
    ('我有时用40Cr。', None),
    ('AI说：记住我的习惯：随意设置尺寸', None),
    ('记住我的习惯：', None),
])
def test_only_explicit_command_records_habit(text, expected):
    assert explicit_preference(text) == expected


def test_project_memory_matches_rephrased_question_and_isolates_scope(setup):
    db, repo, project, _ = setup
    other = ProjectService(repo).create('另一个项目', '', 'OTHER', project_type='test')
    repo.save_project_memory(project.id, '丝杠支承方案', '丝杠安装座采用成对角接触轴承，轴向定位待校核。', '03')
    repo.save_project_memory(project.id, '电气方案', '丝杠电机采用另一种接线。', '07')
    repo.save_project_memory(other.id, '另项目丝杠', '其他项目的丝杠尺寸为99mm。', '03')
    results = repo.list_project_memories(project.id, '03', '丝杠安装座有哪些要点？')
    assert len(results) == 1 and '角接触' in results[0]['content']
    assert repo.list_project_memories(project.id, '03', '防护罩喷漆颜色？') == []


def test_old_relevant_memory_not_lost_behind_300_recent_items(setup):
    _, repo, project, _ = setup
    target = repo.save_project_memory(project.id, '早期目标', '高精度丝杠支承需要预紧。', '03')
    with repo.database.transaction() as c:
        c.executemany('INSERT INTO project_memories(project_id,module_code,title,content,created_at,updated_at) VALUES (?,?,?,?,?,?)',
                      [(project.id, '03', '颜色', f'喷漆记录{i}', 'z', 'z') for i in range(350)])
    assert repo.list_project_memories(project.id, '03', '丝杠支承如何预紧？')[0]['memory_id'] == target


def test_personal_habit_crosses_projects_and_switch_can_disable(setup):
    _, repo, project, shell = setup
    repo.save_design_preference('先给结论，再给计算步骤。')
    repo.save_project_memory(project.id, '轴承方案', '轴承采用6210。', '03')
    new = ProjectService(repo).create('全新项目', '', 'FRESH', project_type='test')
    messages, stats = shell._build_design_chat_messages('轴承方案怎么确定？', {**options(), 'project_id': new.id})
    assert '先给结论' in messages[0]['content'] and '6210' not in messages[0]['content']
    assert stats['preferences'] == 1 and stats['project_memories'] == 0
    messages, _ = shell._build_design_chat_messages('轴承方案怎么确定？', {**options(), 'use_preferences': False})
    assert '先给结论，再给计算步骤。' not in messages[0]['content']


def test_summary_preserves_initial_goal_latest_correction_and_ai_status(setup):
    _, repo, _, shell = setup
    cid = shell.chat_conversation_id
    repo.add_ai_message(cid, 'user', '初始目标：送料机构行程500mm，速度0.2m/s，保持现有安装空间。')
    repo.add_ai_message(cid, 'assistant', '建议选40Cr，尚未得到用户确认。')
    for i in range(80):
        repo.add_ai_message(cid, 'user' if i % 2 == 0 else 'assistant', f'讨论段落{i}：' + '说明细节。' * 120)
    repo.add_ai_message(cid, 'user', '更正：行程改为650mm，电机选型尚未确认。')
    repo.add_ai_message(cid, 'assistant', '建议用42CrMo，需要用户确认。')
    for i in range(12):
        repo.add_ai_message(cid, 'user' if i % 2 == 0 else 'assistant', f'最新消息{i}')
    context = repo.prepare_conversation_context(cid)
    assert '行程500mm' in context['summary'] and '行程改为650mm' in context['summary']
    assert 'AI历史建议（未自动确认或采用）' in context['summary']
    assert len(context['summary']) <= 4000 and len(context['recent_messages']) == 12
    original_count = len(repo.list_ai_messages(cid))
    assert repo.prepare_conversation_context(cid)['summary'] == context['summary']
    assert len(repo.list_ai_messages(cid)) == original_count == 96


def test_long_context_bounded_and_latest_question_not_truncated(setup):
    _, repo, project, shell = setup
    for i in range(28):
        repo.add_ai_message(shell.chat_conversation_id, 'user' if i % 2 == 0 else 'assistant', f'原文{i}：' + '字' * 12000)
    for i in range(8):
        repo.save_design_preference(f'习惯{i}：' + '习惯' * 900)
        repo.save_project_memory(project.id, '轴承设计', '轴承' * 900, '03')
    question = '轴承选型：' + '请依据现有资料。' * 1000
    messages, stats = shell._build_design_chat_messages(question, options())
    assert sum(len(m['content']) for m in messages) <= MAX_CONTEXT_CHARS
    assert messages[-1]['content'] == question and stats['context_reduced']
    assert len(repo.list_ai_messages(shell.chat_conversation_id)[0]['content']) > 12000
    assert messages[0]['content'].startswith('你是机械工程设计问答助手')


def test_oversize_question_rejected_and_original_input_preserved(setup, qtbot, monkeypatch):
    _, repo, _, shell = setup
    shell.design_chat = DesignChatDialog()
    qtbot.addWidget(shell.design_chat)
    shell.main_window = shell.design_chat
    shell.design_chat.input.setPlainText('字' * 12001)
    monkeypatch.setattr(QMessageBox, 'warning', lambda *a: None)
    shell._send_design_chat(shell.design_chat.input.toPlainText(), options())
    assert len(shell.design_chat.input.toPlainText()) == 12001
    assert repo.list_ai_messages(shell.chat_conversation_id) == []


def test_accepted_explicit_preference_persists_without_api_and_does_not_infer_dimensions(setup, qtbot):
    _, repo, _, shell = setup
    shell.design_chat = DesignChatDialog()
    qtbot.addWidget(shell.design_chat)
    shell.main_window = shell.design_chat
    shell.settings = SimpleNamespace(model_name='test')
    shell.credential_store = SimpleNamespace(get_api_key=lambda: '')
    shell._send_design_chat('记住我的习惯：先比较两种方案，再给建议。', options())
    assert repo.list_design_preferences()[0]['content'] == '先比较两种方案，再给建议。'
    shell._send_design_chat('本项目的轴径30mm。', options())
    assert len(repo.list_design_preferences()) == 1
    assert '本项目的轴径30mm。' in repo.list_ai_messages(shell.chat_conversation_id)[-2]['content']


def test_all_reference_switches_off_preserves_current_chat(setup):
    _, repo, _, shell = setup
    repo.add_ai_message(shell.chat_conversation_id, 'user', '当前载荷100N。')
    repo.add_ai_message(shell.chat_conversation_id, 'assistant', '请给行程。')
    repo.save_design_preference('回答尽量短。')
    messages, _ = shell._build_design_chat_messages('行程300mm。', {k: False for k in options()})
    assert [m['role'] for m in messages] == ['system', 'user', 'assistant', 'user']
    assert '载荷100N' in messages[1]['content'] and '回答尽量短' not in messages[0]['content']


def test_no_number_allocation_in_chat_with_existing_assembly(setup, monkeypatch):
    _, _, _, shell = setup
    monkeypatch.setattr(shell.memory, 'allocate_part_number', lambda *a: (_ for _ in ()).throw(AssertionError('不能分配序号')))
    messages, _ = shell._build_design_chat_messages('当前总成如何检查？', options())
    assert messages[-1]['content'] == '当前总成如何检查？'


def test_context_does_not_send_orphan_ai_answer():
    messages = [{'role': 'system', 'content': '工作流程'}, {'role': 'user', 'content': '长问题' * 5000},
                {'role': 'assistant', 'content': '短回答'}, {'role': 'user', 'content': '下一轮问题'}]
    bounded, reduced = bound_messages(messages, maximum=1000)
    assert reduced and [m['role'] for m in bounded] == ['system', 'user', 'assistant', 'user']
    assert '节选' in bounded[1]['content']


def test_context_skips_answer_when_its_user_question_is_outside_recent_window():
    messages = [{'role': 'system', 'content': '流程'}, {'role': 'assistant', 'content': '旧问题的答案'},
                {'role': 'user', 'content': '新问题'}, {'role': 'assistant', 'content': '新答案'},
                {'role': 'user', 'content': '当前问题'}]
    bounded, reduced = bound_messages(messages)
    assert reduced and all(m['content'] != '旧问题的答案' for m in bounded)
    assert [m['role'] for m in bounded] == ['system', 'user', 'assistant', 'user']


def test_memory_menu_seeds_selection_and_is_disabled_during_generation(qtbot):
    dialog = DesignChatDialog()
    qtbot.addWidget(dialog)
    dialog.set_messages([{'role': 'user', 'content': '回答简洁'}, {'role': 'assistant', 'content': '建议采用轴承方案'}])
    spy = QSignalSpy(dialog.save_preference_requested)
    dialog.memory_menu.actions()[0].trigger()
    assert spy[0][0] == '回答简洁'
    assert dialog._memory_seed('assistant') == '建议采用轴承方案'
    dialog.transcript.selectAll()
    assert '回答简洁' in dialog._memory_seed('assistant')
    dialog.set_busy(True)
    assert not dialog.memory_button.isEnabled()
    dialog.set_busy(False)
    assert dialog.memory_button.isEnabled()


def test_save_project_memory_uses_chat_selected_project_and_cancel_keeps_data(setup, qtbot, monkeypatch):
    _, repo, project, shell = setup
    other = ProjectService(repo).create('菜单所选项目', '', 'MENU', project_type='test')
    shell.design_chat = DesignChatDialog()
    qtbot.addWidget(shell.design_chat)
    shell.main_window = shell.design_chat
    shell.design_chat.set_projects(repo.list_projects(), other.id)
    shell.design_chat.set_modules([{'module_code': '07', 'module_name': '电气'}], '07')
    monkeypatch.setattr(QInputDialog, 'getMultiLineText', lambda *a: ('轴承采用6210，待校核载荷。', True))
    shell._save_chat_project_memory('AI原始建议')
    assert repo.list_project_memories(project.id) == []
    assert repo.list_project_memories(other.id, '07')[0]['content'] == '轴承采用6210，待校核载荷。'
    monkeypatch.setattr(QInputDialog, 'getMultiLineText', lambda *a: ('误操作', False))
    shell._save_chat_preference('误操作')
    assert repo.list_design_preferences() == []


def test_memory_manager_edit_delete_keeps_original_chat_and_other_project(setup, qtbot, monkeypatch):
    _, repo, project, shell = setup
    mid = repo.add_ai_message(shell.chat_conversation_id, 'user', '原始聊天不可删')
    repo.save_design_preference('先给结论', mid)
    repo.save_project_memory(project.id, '设计结论', '轴承采用6210', '03', mid)
    dialog = DesignMemoryDialog(repo, project.id)
    qtbot.addWidget(dialog)
    dialog.table.selectRow(0)
    monkeypatch.setattr(QInputDialog, 'getMultiLineText', lambda *a: ('先给结论，再列公式', True))
    dialog._edit()
    assert repo.list_design_preferences()[0]['content'] == '先给结论，再列公式'
    dialog.table.selectRow(0)
    monkeypatch.setattr(QMessageBox, 'question', lambda *a: QMessageBox.StandardButton.No)
    dialog._delete()
    assert len(repo.list_design_preferences()) == 1
    monkeypatch.setattr(QMessageBox, 'question', lambda *a: QMessageBox.StandardButton.Yes)
    dialog._delete()
    assert repo.list_design_preferences() == []
    dialog.table.selectRow(0)
    dialog._delete()
    assert repo.list_project_memories(project.id) == []
    assert repo.list_ai_messages(shell.chat_conversation_id)[0]['content'] == '原始聊天不可删'


@pytest.mark.parametrize('size', [(820, 540), (980, 680), (1280, 720)])
def test_new_chat_controls_remain_usable_at_compact_sizes(qtbot, size):
    dialog = DesignChatDialog()
    qtbot.addWidget(dialog)
    dialog.resize(*size)
    dialog.show()
    qtbot.wait(10)
    assert dialog.use_preferences.isVisible() and dialog.memory_button.isVisible()
    assert dialog.memory_button.geometry().right() <= dialog.width()
    assert dialog.input.height() > 40 and dialog.transcript.height() > 100
