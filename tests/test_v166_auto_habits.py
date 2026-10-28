# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 zhang18166

from types import SimpleNamespace

import pytest

from mechanical_project_namer.ai import NamingMemory
from mechanical_project_namer.ai.habit_learning import detect_habits
from mechanical_project_namer.data import Database, Repository
from mechanical_project_namer.projects import ProjectService
from mechanical_project_namer.ui.design_chat_dialog import DesignChatDialog
from mechanical_project_namer.ui.design_memory_dialog import DesignMemoryDialog
from mechanical_project_namer.ui.shell import ApplicationShell


@pytest.fixture
def repo(tmp_path):
    db = Database(tmp_path / 'learning.db')
    db.migrate()
    return Repository(db)


def send(repo, text, cid=None, role='user'):
    if cid is None:
        cid = repo.create_conversation(None, '测试', 'test', False, 'high')
    mid = repo.add_ai_message(cid, role, text)
    return repo.learn_design_habits(mid)


@pytest.mark.parametrize('text', ['以后回答简洁些。', '我喜欢回答简短些。', '今后先给结论，再说依据。', '以后计算时列出公式。', '每次只问一个关键问题。'])
def test_clear_natural_preference_saved_without_special_command(repo, text):
    assert send(repo, text)
    pref = repo.list_design_preferences()[0]
    assert pref['source'] == 'auto' and pref['source_message_id'] is not None


@pytest.mark.parametrize('text', ['本项目的轴径30mm，材料40Cr。', '我习惯用6210轴承。', '这个项目以后回答详细些。', '这次回答简短点。', '暂时先给结论。', '例如以后回答简短点。', 'AI说：以后回答简短点。', '以后回答不要记到习惯里。', '客户说以后回答详细点。', '如果以后回答简洁些会怎么样？', '我希望零件名称简短些。'])
def test_project_values_temporary_requests_and_quoted_examples_not_learned(repo, text):
    assert send(repo, text) == []
    assert repo.list_design_preferences() == []


def test_single_feedback_not_global_but_repeated_feedback_learned(repo):
    assert send(repo, '回答太啰嗦了。') == []
    assert send(repo, '回答简短点。')
    rows = repo.list_design_preferences()
    assert len(rows) == 1 and rows[0]['content'] == '回答保持简洁。' and rows[0]['evidence_count'] == 2
    send(repo, '以后回答简洁些。')
    assert len(repo.list_design_preferences()) == 1


def test_learning_only_user_feedback_and_not_explicit_command(repo):
    assert send(repo, '以后回答简洁些。', role='assistant') == []
    assert send(repo, '记住我的习惯：回答简短些。') == []
    assert repo.list_design_preferences() == []


@pytest.mark.parametrize('text', ['以后别先给结论。', '以后计算不要列公式。', '每次不要只问一个问题。', '以后不用中文回答。'])
def test_negated_preferences_never_learn_the_opposite(repo, text):
    assert send(repo, text) == []


def test_do_not_be_detailed_learns_concise_style(repo):
    send(repo, '以后回答不要太详细。')
    assert repo.list_design_preferences()[0]['content'] == '回答保持简洁。'


def test_auto_conflict_requires_new_evidence_and_keeps_same_row(repo):
    send(repo, '以后回答简洁些。')
    pid = repo.list_design_preferences()[0]['preference_id']
    assert send(repo, '回答详细一点。') == []
    send(repo, '回答详细些。')
    rows = repo.list_design_preferences()
    assert len(rows) == 1 and rows[0]['preference_id'] == pid and '详细' in rows[0]['content']
    send(repo, '以后回答简洁些。')
    assert '简洁' in repo.list_design_preferences()[0]['content']


def test_auto_never_overwrites_fixed_manual_preference(repo):
    repo.save_design_preference('回答详细说明依据和步骤。')
    notice = send(repo, '以后回答简洁些。')
    assert '未自动覆盖' in notice[0]
    assert len(repo.list_design_preferences()) == 1 and '详细' in repo.list_design_preferences()[0]['content']


def test_explicit_command_takes_priority_over_old_auto_memory(repo):
    send(repo, '以后回答简洁些。')
    cid = repo.create_conversation(None, '明确改动', 'm', False, 'high')
    repo.add_ai_message(cid, 'user', '记住我的习惯：回答详细些。', design_preference='回答详细些。')
    active = repo.list_design_preferences('回答')
    assert len(active) == 1 and active[0]['source'] == 'explicit'
    assert len(repo.list_design_preferences()) == 2  # Management keeps both visible.


def test_switch_persists_and_old_disabled_feedback_not_used_after_reenable(repo):
    repo.set_auto_habit_learning(False)
    assert send(repo, '回答太啰嗦了。') == []
    reopened = Repository(Database(repo.database.path))
    assert not reopened.auto_habit_learning_enabled()
    reopened.set_auto_habit_learning(True)
    assert send(reopened, '回答太啰嗦了。') == []
    assert send(reopened, '回答简短点。')


def test_deleted_auto_memory_not_relearned_from_old_chat(repo):
    send(repo, '回答太啰嗦了。')
    send(repo, '回答简短点。')
    repo.delete_design_preference(repo.list_design_preferences()[0]['preference_id'])
    assert send(repo, '回答简短点。') == []
    assert repo.list_design_preferences() == []
    assert send(repo, '回答简短点。')


def test_repeated_feedback_bounded_history(repo):
    send(repo, '回答简短点。')
    cid = repo.create_conversation(None, '连续测试', 'm', False, 'high')
    for i in range(62):
        repo.add_ai_message(cid, 'user', f'普通问题{i}')
    assert send(repo, '回答简短点。') == []


def test_shell_saves_natural_preference_and_reports_result(repo, qtbot):
    shell = object.__new__(ApplicationShell)
    shell.repository = repo
    shell.memory = NamingMemory(repo.database)
    shell.current_project_id = ProjectService(repo).create('临时项目', '', 'AUTO', project_type='test').id
    shell.current_module_code = '03'
    shell.chat_conversation_id = repo.create_conversation(shell.current_project_id, '新对话', 'm', False, 'high')
    shell.design_chat = DesignChatDialog()
    qtbot.addWidget(shell.design_chat)
    shell.main_window = shell.design_chat
    shell.settings = SimpleNamespace(model_name='test')
    shell.credential_store = SimpleNamespace(get_api_key=lambda: '')
    opts = dict(use_project=False, use_history=False, use_naming=False, use_memory=False, auto_learning=True)
    shell._send_design_chat('以后回答简洁些。', opts)
    assert repo.list_design_preferences()[0]['source'] == 'auto'
    assert '自动记住' in shell.design_chat.reference_label.text()
    manager = DesignMemoryDialog(repo, shell.current_project_id)
    qtbot.addWidget(manager)
    assert '自动学习' in manager.table.item(0, 2).text()
    shell._send_design_chat('以后计算时列出公式。', {**opts, 'auto_learning': False})
    assert len(repo.list_design_preferences()) == 1


def test_old_schema8_preferences_preserved_and_added_columns_backed_up(repo):
    repo.save_design_preference('旧习惯原文不变')
    with repo.database.transaction() as c:
        for name in ('habit_key', 'source', 'evidence_count'):
            c.execute(f'ALTER TABLE design_preferences DROP COLUMN {name}')
        c.execute('UPDATE schema_meta SET version=8')
    repo.database.migrate()
    row = repo.list_design_preferences()[0]
    assert row['content'] == '旧习惯原文不变' and row['source'] == 'manual'
    assert list((repo.database.path.parent / 'backups/database').glob('*pre_schema*.db'))
