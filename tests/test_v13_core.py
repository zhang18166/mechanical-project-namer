# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 zhang18166

from pathlib import Path

import pytest

from mechanical_project_namer.data.database import Database
from mechanical_project_namer.data.repository import Repository
from mechanical_project_namer.numbering.v12 import MODULES


@pytest.fixture
def repo(tmp_path):
    database = Database(Path(tmp_path) / "v13.db")
    database.migrate()
    return Repository(database)


def test_new_project_seeds_default_modules_and_custom_module(repo):
    project = repo.create_project("测试设备", "", "TST")
    assert len(repo.list_modules(project.id, enabled_only=False)) == len(MODULES)
    created = repo.create_module(project.id, "10", "检测模块")
    assert created["source"] == "user"
    assert repo.list_modules(project.id, enabled_only=False)[-1]["module_code"] == "10"


def test_module_disable_and_used_delete_guard(repo):
    project = repo.create_project("测试设备", "", "TST2")
    repo.set_module_enabled(project.id, "03", False)
    assert not [x for x in repo.list_modules(project.id) if x["module_code"] == "03"]
    repo.set_module_enabled(project.id, "03", True)
    repo.save_named_record(project.id, "03", "自制机加工件", "压弧滑座")
    with pytest.raises(ValueError):
        repo.delete_module(project.id, "03")


def test_ai_plan_conversation_and_project_memory_are_persistent(repo):
    project = repo.create_project("测试设备", "", "TST3")
    plan_id = repo.save_ai_plan(prompt="规划设备", result=None, project_id=project.id)
    assert repo.get_ai_plan(plan_id)["prompt"] == "规划设备"
    conversation_id = repo.create_conversation(project.id, "结构讨论", "deepseek-v4-pro", True, "high")
    message_id = repo.add_ai_message(conversation_id, "user", "如何选择导轨？")
    repo.add_ai_message(conversation_id, "assistant", "请补充载荷和速度。")
    assert repo.list_ai_messages(conversation_id)[0]["message_id"] == message_id
    memory_id = repo.save_project_memory(project.id, "导轨约束", "需要补充载荷", "03", message_id)
    assert repo.list_project_memories(project.id, "03")[0]["memory_id"] == memory_id


def test_adopt_modules_only_adds_selected_and_reports_conflicts(repo):
    project = repo.create_project("测试设备", "", "TST4")
    result = repo.adopt_modules(project.id, [{"module_code": "10", "module_name": "检测模块"}, {"module_code": "03", "module_name": "压弧模块"}])
    assert "10" in result["added"]
    assert result["conflicts"]
    names = {x["module_code"]: x["module_name"] for x in repo.list_modules(project.id, enabled_only=False)}
    assert names["03"] == "压弧/成型模块"
