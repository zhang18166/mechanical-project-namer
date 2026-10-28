# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 zhang18166

import json
from threading import Event
from types import SimpleNamespace

import httpx
import pytest
from PyQt6.QtWidgets import QPushButton

from mechanical_project_namer.ai import AICancelled, AIClient, AIClientError, NamingMemory
from mechanical_project_namer.ai.quality import assess_naming_quality
from mechanical_project_namer.ai.models import CodeSuggestion
from mechanical_project_namer.ai.models import StreamEvent
from mechanical_project_namer.ai.worker import AIWorker
from mechanical_project_namer.ai.runtime import sanitize_log_message
from mechanical_project_namer.data import Database, Repository
from mechanical_project_namer.projects import ProjectService
from mechanical_project_namer.security import CredentialStore
from mechanical_project_namer.settings import AppSettings, load_settings, save_settings
from mechanical_project_namer.ui.main_window import MainWindow
from mechanical_project_namer.ui.settings_page import SettingsPage
from mechanical_project_namer.ui.shell import ApplicationShell


class FakeCredentialBackend:
    def __init__(self, value=None):
        self.value = value

    def get_password(self, service, account):
        return self.value

    def set_password(self, service, account, value):
        self.value = value

    def delete_password(self, service, account):
        self.value = None


def make_client(handler, **kwargs):
    transport = httpx.MockTransport(handler)
    return AIClient("https://api.deepseek.com", "deepseek-v4-pro", "sk-test-secret", http_client=httpx.Client(transport=transport), **kwargs)


def test_key_save_delete_and_masked_status():
    backend = FakeCredentialBackend()
    store = CredentialStore(backend)
    assert store.is_configured() is False
    store.set_api_key("sk-1234567890A32F")
    assert store.is_configured() is True
    assert "A32F" in store.masked_status()
    assert "1234567890" not in store.masked_status()
    store.delete_api_key()
    assert store.get_api_key() is None


def test_no_key_has_explicit_error():
    with pytest.raises(AIClientError, match="未配置 API Key"):
        AIClient("https://api.deepseek.com", "deepseek-v4-pro", "").discover_models()


def test_model_discovery_reads_real_ids_and_balance():
    def handler(request):
        if request.url.path == "/models":
            return httpx.Response(200, json={"data": [{"id": "deepseek-v4-pro"}, {"id": "deepseek-v4-flash"}]})
        return httpx.Response(200, json={"is_available": True, "balance_infos": [{"currency": "CNY", "total_balance": "12.50", "granted_balance": "2.50", "topped_up_balance": "10.00"}]})

    client = make_client(handler)
    assert [item.model_id for item in client.discover_models()] == ["deepseek-v4-pro", "deepseek-v4-flash"]
    balance = client.balance()
    assert balance.is_available is True
    assert balance.total_balance == "12.50"


def test_model_discovery_failure_is_diagnosable():
    def handler(request):
        return httpx.Response(401, json={"error": {"message": "invalid"}})

    result = make_client(handler).check_key_network()
    assert result.ok is False
    assert result.http_status == 401
    assert "HTTP 401" in result.message


def test_streaming_events_and_model_metrics():
    payload = json.dumps({"model": "deepseek-v4-pro", "choices": [{"delta": {"content": "连接"}}]})
    payload2 = json.dumps({"model": "deepseek-v4-pro", "choices": [{"delta": {"content": "成功"}}], "usage": {"prompt_tokens": 4, "completion_tokens": 2, "prompt_tokens_details": {"cached_tokens": 1}}})

    def handler(request):
        assert json.loads(request.content)["stream"] is True
        body = f"data: {payload}\n\ndata: {payload2}\n\ndata: [DONE]\n\n".encode()
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=body)

    events = []
    result = make_client(handler).test_model(emit=events.append)
    assert result.reply == "连接成功"
    assert result.actual_model == "deepseek-v4-pro"
    assert result.cached_tokens == 1
    assert {event.kind for event in events} >= {"submitted", "accepted", "generating"}


def test_model_probe_does_not_require_json_response_format():
    seen = {}

    def handler(request):
        seen["payload"] = json.loads(request.content)
        body = 'data: ' + json.dumps({"model": "deepseek-v4-pro", "choices": [{"delta": {"content": "连接成功"}}]}) + '\n\ndata: [DONE]\n\n'
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=body.encode())

    result = make_client(handler).test_model()
    assert result.reply == "连接成功"
    assert "response_format" not in seen["payload"]


def test_model_probe_rejects_empty_reply():
    def handler(_request):
        body = 'data: ' + json.dumps({"model": "deepseek-v4-pro", "choices": [{"delta": {}}], "usage": {"completion_tokens": 2}}) + '\n\ndata: [DONE]\n\n'
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=body.encode())

    with pytest.raises(AIClientError) as error:
        make_client(handler).test_model()
    assert error.value.code == "empty_response"
    assert error.value.metrics["completion_tokens"] == 2


def test_max_model_probe_uses_larger_reasoning_budget():
    seen = {}

    def handler(request):
        seen.update(json.loads(request.content))
        body = 'data: ' + json.dumps({"model": "deepseek-v4-pro", "choices": [{"delta": {"content": "连接成功"}}]}) + '\n\ndata: [DONE]\n\n'
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=body.encode())

    make_client(handler, reasoning_level="max").test_model()
    assert seen["max_tokens"] == 1024


def test_fabricated_key_is_never_in_error_text():
    secret = "sk-super-secret-value"

    def handler(request):
        return httpx.Response(403, json={"error": {"message": secret}})

    client = AIClient("https://api.deepseek.com", "deepseek-v4-pro", secret, http_client=httpx.Client(transport=httpx.MockTransport(handler)))
    with pytest.raises(AIClientError) as error:
        client.discover_models()
    assert secret not in str(error.value)


def test_runtime_log_sanitizes_key_and_authorization():
    value = sanitize_log_message("Authorization: Bearer sk-super-secret-value / sk-another-secret")
    assert "super-secret" not in value
    assert "another-secret" not in value
    assert "[已隐藏]" in value


def test_stream_cancel_is_distinct():
    cancel = Event(); cancel.set()
    with pytest.raises(AICancelled):
        make_client(lambda request: httpx.Response(200, json={})).test_model(cancel)


def test_worker_reports_status_and_exception():
    statuses = []
    failures = []
    finished = []
    worker = AIWorker(lambda cancel, emit: (_ for _ in ()).throw(AIClientError("模型不可用", code="model_unavailable", stage="测试所选模型")))
    worker.status.connect(statuses.append)
    worker.failed.connect(failures.append)
    worker.finished.connect(lambda: finished.append(True))
    worker.run()
    assert statuses[0].kind == "started"
    assert failures[0].code == "model_unavailable"
    assert finished == [True]


def test_worker_cancel_event_is_set():
    observed = []
    worker = AIWorker(lambda cancel, emit: observed.append(cancel.is_set()))
    worker.cancel()
    worker.run()
    assert observed == [True]


def test_settings_remember_model_thinking_and_timeouts(tmp_path):
    target = tmp_path / "settings.json"
    settings = AppSettings(model_name="deepseek-v4-pro", available_models=["deepseek-v4-pro"], model_list_source="缓存模型列表", thinking_mode="enabled", reasoning_level="max", connect_timeout=10, request_timeout=30, generation_timeout=180)
    save_settings(settings, target)
    loaded = load_settings(target)
    assert loaded.model_name == "deepseek-v4-pro"
    assert loaded.available_models == ["deepseek-v4-pro"]
    assert loaded.reasoning_level == "max"
    assert loaded.generation_timeout == 180
    assert "api_key" not in target.read_text(encoding="utf-8")


def test_ai_button_immediately_enters_busy_state(qtbot):
    window = MainWindow(AppSettings())
    qtbot.addWidget(window)
    window.show()
    qtbot.waitExposed(window)
    window.set_ai_busy(True)
    window.set_ai_status("正在准备 AI 请求……", 0.1)
    assert window.ai_button.isEnabled() is False
    assert window.ai_cancel.isVisible() is True
    assert "正在准备" in window.ai_status.text()


def test_settings_ai_operation_has_immediate_feedback_and_cancel(qtbot):
    page = SettingsPage(AppSettings())
    qtbot.addWidget(page)
    page.show()
    qtbot.waitExposed(page)

    page.set_ai_busy(True, "测试所选模型")

    assert all(not button.isEnabled() for button in page.ai_action_buttons)
    assert page.ai_cancel.isVisible() is True
    assert "正在启动" in page.diagnostic_label.text()

    page.set_ai_busy(False)
    assert all(button.isEnabled() for button in page.ai_action_buttons)
    assert page.ai_cancel.isVisible() is False


def test_ai_statistics_block_contains_token_cache_timing_and_balance():
    lines = []
    shell = object.__new__(ApplicationShell)
    shell.ai_statistics_written = False
    shell.ai_started_at = 0.0
    shell.ai_metrics = {
        "model": "deepseek-v4-pro", "reasoning_level": "high",
        "prompt_tokens": 2146, "completion_tokens": 682, "cached_tokens": 1520,
        "first_response_seconds": 1.28, "first_text_seconds": 3.41, "elapsed_seconds": 14.62,
    }
    shell.latest_balance = SimpleNamespace(currency="CNY", total_balance="105.33")
    shell.main_window = SimpleNamespace(append_ai_log=lines.append)
    shell._write_ai_statistics("成功")
    output = lines[0]
    assert "【本次 AI 使用统计】" in output
    assert "输入 Token：2,146" in output
    assert "缓存未命中 Token：626" in output
    assert "缓存命中率：70.83%" in output
    assert "预计费用：暂无数据" in output
    assert "API 剩余余额：CNY 105.33" in output


def test_ai_finish_replaces_stale_running_status():
    statuses = []
    shell = object.__new__(ApplicationShell)
    shell._capture_result_metrics = lambda _result: None
    shell._write_ai_statistics = lambda _status: None
    shell.main_window = SimpleNamespace(
        set_ai_busy=lambda _busy: None,
        set_ai_status=statuses.append,
        settings_page=SimpleNamespace(set_ai_busy=lambda _busy: None),
    )
    shell.mini_window = SimpleNamespace(set_ai_status=lambda _status: None)
    shell.ai_elapsed_timer = SimpleNamespace(stop=lambda: None)
    shell.settings_ai_operation_active = True
    shell.ai_worker = object()

    shell._finish_ai_ui(status="成功")

    assert statuses == ["成功"]


def test_json_parse_failure_log_includes_audit_record_and_path(monkeypatch, tmp_path):
    lines = []
    diagnostic = []
    shell = object.__new__(ApplicationShell)
    shell.memory_package = SimpleNamespace(response_log_path=tmp_path / "ai_raw_responses.jsonl")
    shell.main_window = SimpleNamespace(settings_page=SimpleNamespace(set_diagnostic=diagnostic.append))
    shell._append_ai_log = lines.append
    shell._finish_ai_ui = lambda *_args, **_kwargs: None
    monkeypatch.setattr("mechanical_project_namer.ui.shell.QMessageBox.warning", lambda *_args: None)
    error = AIClientError(
        "JSON 解析失败", code="json_parse", stage="解析结果",
        metrics={"response_record_id": "audit-123"},
    )

    shell._on_ai_error(error)

    output = "\n".join(lines)
    assert "audit-123" in output
    assert str(shell.memory_package.response_log_path) in output
    assert diagnostic and "解析结果" in diagnostic[0]


def test_thread_cleanup_does_not_overwrite_finished_status():
    class CleanupThread:
        def __init__(self):
            self.deleted = False

        def deleteLater(self):
            self.deleted = True

    thread = CleanupThread()
    shell = object.__new__(ApplicationShell)
    shell.ai_threads = {thread}
    shell.ai_thread = thread
    shell._finish_ai_ui = lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("must not finish twice"))

    shell._clear_ai_thread(thread)

    assert thread.deleted is True
    assert shell.ai_thread is None


def test_dropdown_model_selection_clears_stale_manual_override(qtbot):
    page = SettingsPage(AppSettings(model_name="manual-model"))
    qtbot.addWidget(page)
    page.manual_model.setText("manual-model")
    page.set_model_values(["deepseek-v4-pro", "deepseek-v4-flash"], "实时模型列表", "")
    page.model_combo.setCurrentIndex(1)
    assert page.values()["model_name"] == "deepseek-v4-flash"


@pytest.mark.parametrize("window_size", [(1366, 768), (1920, 1080), (2560, 1440)])
def test_settings_ai_controls_do_not_overlap_at_target_resolutions(qtbot, window_size):
    window = MainWindow(AppSettings())
    qtbot.addWidget(window)
    window.resize(*window_size)
    window.show_page(3)
    window.show()
    qtbot.waitExposed(window)
    page = window.settings_page
    controls = [
        page.api_url, page.api_key, page.model_combo, page.manual_model,
        page.thinking, page.reasoning, page.connect_timeout,
        page.request_timeout, page.generation_timeout,
    ]
    visible = [control for control in controls if control.isVisible()]
    rects = []
    for control in visible:
        rect = control.rect()
        rect.moveTopLeft(control.mapTo(page, rect.topLeft()))
        rects.append(rect)
    assert all(control.height() >= 30 for control in visible)
    assert all(not left.intersects(right) for index, left in enumerate(rects) for right in rects[index + 1:])


@pytest.mark.parametrize("window_size", [(1366, 768), (1920, 1080), (2560, 1440)])
def test_memory_actions_fit_at_target_resolutions(qtbot, window_size):
    window = MainWindow(AppSettings())
    qtbot.addWidget(window)
    window.resize(*window_size)
    window.show_page(3)
    window.show()
    qtbot.waitExposed(window)
    page = window.settings_page
    page.tabs.setCurrentIndex(1)
    buttons = [button for button in page.findChildren(QPushButton) if button.property("memoryAction")]
    assert len(buttons) == 13
    assert all(button.isVisible() for button in buttons)
    assert all(button.width() >= button.sizeHint().width() for button in buttons)
    rects = []
    for button in buttons:
        rect = button.rect()
        rect.moveTopLeft(button.mapTo(page, rect.topLeft()))
        rects.append(rect)
    assert all(not left.intersects(right) for index, left in enumerate(rects) for right in rects[index + 1:])


@pytest.fixture()
def memory_services(tmp_path):
    database = Database(tmp_path / "data.db")
    database.migrate()
    repository = Repository(database)
    project = ProjectService(repository).create("圆管自动制圈机", "压弧伺服设备", "ZGQ")
    memory = NamingMemory(database)
    return database, repository, project, memory


def test_default_fixed_rules_are_seeded(memory_services):
    _, _, _, memory = memory_services
    rules = memory.rules()
    assert len(rules) >= 5
    assert any(item["rule_key"] == "format" and "模块编号" in item["content"] for item in rules)


def test_context_retrieves_similar_project_part_and_feedback(memory_services):
    _, repository, project, memory = memory_services
    memory.save_part(project.id, "03", 1, "压弧轮", "自制机加工件", "压弧成型")
    repository.save_next_record(project.id, "压弧滑座", "伺服推动沿导向移动")
    memory.record_feedback(project.id, "03", "压弧移动底座", "压弧滑座", "edited")
    context = memory.build_context("压弧移动座 伺服", project.id, "03")
    assert context.current_project["code"] == "ZGQ"
    assert context.current_parts[0]["part_name"] == "压弧轮"
    assert context.feedback[0]["final_name"] == "压弧滑座"
    assert "固定命名规则" in context.prompt_summary
    assert context.module_code == "03"
    assert context.part_type == "自制机加工件"
    assert context.next_part_number == 2
    assert "AI 不得生成或修改序号" in context.prompt_summary


def test_confirmed_drawing_record_is_available_to_other_projects(memory_services):
    _, repository, project, memory = memory_services
    repository.save_next_record(project.id, "压弧滑座", "沿直线导轨往复移动")
    other = ProjectService(repository).create("圆管校圆机", "压弧成型设备", "JYJ")
    context = memory.build_context("压弧导轨移动承载零件", other.id, "03")
    assert any(item["part_name"] == "压弧滑座" for item in context.similar_parts)


def test_irrelevant_feedback_is_not_injected(memory_services):
    _, _, project, memory = memory_services
    memory.record_feedback(project.id, "02", "送料移动底座", "送料滑座", "edited")
    memory.record_feedback(project.id, "03", "压弧移动底座", "压弧滑座", "edited")
    context = memory.build_context("压弧模块导轨上的往复承载座", project.id, "03")
    assert [item["final_name"] for item in context.feedback] == ["压弧滑座"]


def test_quality_assessment_prefers_concise_historical_name(memory_services):
    _, _, project, memory = memory_services
    memory.record_feedback(project.id, "03", "压弧移动底座", "压弧滑座", "edited")
    context = memory.build_context("压弧模块导轨上的往复承载座", project.id, "03")
    suggestion = CodeSuggestion("ZGQ", (), "", "", "无", "03", "自制机加工件", "压弧滑座", "")
    quality = assess_naming_quality((suggestion,), context)
    assert quality["规则符合度"] == "高"
    assert quality["历史一致性"] == "高"
    assert quality["名称简洁度"] == "高"


def test_local_part_numbering_is_independent_and_bounded(memory_services):
    _, _, project, memory = memory_services
    assert memory.allocate_part_number(project.id, "03", "自制机加工件") == 1
    memory.save_part(project.id, "03", 1, "压弧轮", "自制机加工件")
    assert memory.allocate_part_number(project.id, "03", "自制机加工件") == 2
    memory.save_part(project.id, "03", 2, "压弧轮轴", "自制机加工件")
    assert memory.allocate_part_number(project.id, "03", "自制机加工件") == 3


def test_feedback_actions_and_clear_do_not_delete_formal_project(memory_services):
    _, repository, project, memory = memory_services
    memory.record_feedback(project.id, "03", "板1", "压弧滑座", "edited")
    memory.record_feedback(project.id, "03", "压弧底座", "", "rejected", "正式名称太泛")
    assert len(memory.feedback()) == 2
    memory.clear_learning()
    assert memory.feedback() == []
    assert repository.get_project(project.id).name == "圆管自动制圈机"


def test_repeated_feedback_increments_use_count_and_preference(memory_services):
    _, _, project, memory = memory_services
    first = memory.record_feedback(project.id, "03", "压弧移动底座", "压弧滑座", "edited")
    second = memory.record_feedback(project.id, "03", "压弧移动底座", "压弧滑座", "edited")
    assert first == second
    row = memory.feedback()[0]
    assert row["use_count"] == 2
    assert memory.preferred_correction("压弧移动底座", "03") == "压弧滑座"


def test_project_can_be_excluded_from_ai_reference(memory_services):
    _, _, project, memory = memory_services
    assert memory.project_reference_allowed(project.id) is True
    memory.set_project_reference(project.id, False)
    assert memory.project_reference_allowed(project.id) is False
    context = memory.build_context("圆管自动制圈机")
    assert all(item["code"] != "ZGQ" for item in context.similar_projects)


def test_balance_unavailable_is_distinct_from_invalid_key():
    def handler(request):
        return httpx.Response(200, json={"is_available": False, "balance_infos": [{"currency": "CNY", "total_balance": "0.00"}]})

    result = make_client(handler).balance()
    assert result.is_available is False
    assert result.total_balance == "0.00"


def test_model_list_timeout_has_specific_code():
    def handler(request):
        raise httpx.ConnectTimeout("timeout", request=request)

    with pytest.raises(AIClientError) as error:
        make_client(handler).discover_models()
    assert error.value.code == "connect_timeout"


def test_rule_edit_and_reset(memory_services):
    _, _, _, memory = memory_services
    original = next(item["content"] for item in memory.rules() if item["rule_key"] == "name_style")
    memory.update_rule("name_style", "用户新增规则：优先使用功能+类型")
    assert any(item["content"].startswith("用户新增") for item in memory.rules())
    memory.reset_rules()
    assert next(item["content"] for item in memory.rules() if item["rule_key"] == "name_style") == original
