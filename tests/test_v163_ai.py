# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 zhang18166

"""The API defaults to thinking; every disabled request must override it."""
import json

import httpx
import pytest

from mechanical_project_namer.ai import AIClient
from mechanical_project_namer.ai.memory import MemoryContext


def response(text, finish="stop"):
    event = {"model": "deepseek-flash", "choices": [{"delta": {"content": text}, "finish_reason": finish}]}
    return httpx.Response(200, headers={"content-type": "text/event-stream"},
                          content=f"data: {json.dumps(event)}\n\ndata: [DONE]\n\n".encode())


@pytest.mark.parametrize("route", ["model", "project", "part", "chat"])
def test_disabled_mode_overrides_server_default_for_all_public_routes(route):
    payloads = []
    def server(request):
        payload = json.loads(request.content)
        payloads.append(payload)
        # Model the provider's enabled default and exhausted thinking budget.
        if payload.get("thinking", {"type": "enabled"})["type"] == "enabled":
            return response("", "length")
        if route == "model":
            text = "连接成功"
        elif route == "chat":
            text = "轴承安装座用于定位和支撑轴承。"
        else:
            suggestions = [
                {"code": "TSTFIX" if route == "part" else code, "segments": [],
                 "overall_meaning": "测试设备", "module_code": "03", "part_type": "自制机加工件",
                 "recommended_name": name, "reason": "按安装功能命名"}
                for code, name in (("TSTFIX", "轴承安装座"), ("TSTALT", "轴承支撑座"), ("TSTEXP", "轴承固定座"))
            ]
            text = json.dumps({"suggestions": suggestions}, ensure_ascii=False)
        return response(text)
    with httpx.Client(transport=httpx.MockTransport(server)) as http:
        client = AIClient("https://api.deepseek.com", "deepseek-v4-flash", "test-key",
                          thinking_enabled=False, reasoning_level="max", http_client=http)
        if route == "model":
            assert client.test_model().reply == "连接成功"
        elif route == "chat":
            assert client.engineering_chat_stream([{"role": "user", "content": "测试"}])["content"]
        else:
            context = MemoryContext((), {"name": "测试项目", "code": "TSTFIX"}, (), (), (), (), "03", "自制机加工件")
            result = client.suggest_stream("测试设备的轴承安装座", context=context, task_type=route)
            assert len(result.suggestions) == 3
        assert len(payloads) == 1
        assert payloads[0]["thinking"] == {"type": "disabled"}
        assert "reasoning_effort" not in payloads[0]


@pytest.mark.parametrize("effort", ["low", "high", "max"])
def test_enabled_mode_keeps_selected_effort(effort):
    payloads = []
    def server(request):
        payloads.append(json.loads(request.content))
        return response("连接成功")
    with httpx.Client(transport=httpx.MockTransport(server)) as http:
        client = AIClient("https://api.deepseek.com", "deepseek-v4-pro", "test-key",
                          thinking_enabled=True, reasoning_level=effort, http_client=http)
        assert client.test_model().reply == "连接成功"
    assert payloads[0]["thinking"] == {"type": "enabled"}
    assert payloads[0]["reasoning_effort"] == effort


@pytest.mark.parametrize("configured,override", [(True, False), (False, True)])
def test_per_request_override_controls_payload_and_reported_mode(configured, override):
    payloads, events = [], []
    def server(request):
        payloads.append(json.loads(request.content))
        return response("ok")
    with httpx.Client(transport=httpx.MockTransport(server)) as http:
        client = AIClient("https://api.deepseek.com", "deepseek-flash", "test-key",
                          thinking_enabled=configured, http_client=http)
        result = client._stream_request([{"role": "user", "content": "测试"}], max_tokens=256,
                                        cancel_event=None, emit=events.append, thinking_enabled=override)
    assert result[0] == "ok"
    assert payloads[0]["thinking"] == {"type": "enabled" if override else "disabled"}
    assert all(event.data["thinking_enabled"] is override for event in events if event.data)
