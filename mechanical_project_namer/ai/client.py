# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 zhang18166

import json
import time
from collections.abc import Callable
from threading import Event

import httpx

from ..development import is_development

from .models import AISuggestionResult, BalanceInfo, DiagnosticResult, ModelInfo, ModelTestResult, StreamEvent
from .prompt_builder import build_messages
from .quality import assess_naming_quality
from .response_audit import AIResponseAudit
from .response_parser import AIResponseError, parse_suggestions


class AIClientError(RuntimeError):
    def __init__(self, message: str, *, code: str = "internal", stage: str = "请求",
                 status: int | None = None, metrics: dict | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.stage = stage
        self.status = status
        self.metrics = metrics or {}


class AICancelled(AIClientError):
    def __init__(self, metrics: dict | None = None) -> None:
        super().__init__("用户取消了 AI 请求。", code="cancelled", stage="取消", metrics=metrics)


class AIClient:
    def __init__(self, api_url: str, model_name: str, api_key: str, *, connect_timeout: float = 10,
                 request_timeout: float = 30, generation_timeout: float = 180,
                 thinking_enabled: bool = True, reasoning_level: str = "high",
                 auto_repair_enabled: bool = True,
                 http_client: httpx.Client | None = None,
                 response_audit: AIResponseAudit | None = None) -> None:
        self.api_url = api_url.strip().rstrip("/")
        if self.api_url.endswith("/chat/completions"):
            self.api_url = self.api_url[: -len("/chat/completions")]
        self.model_name = model_name.strip()
        self.api_key = api_key.strip()
        self.connect_timeout = connect_timeout
        self.request_timeout = request_timeout
        self.generation_timeout = generation_timeout
        self.thinking_enabled = thinking_enabled
        self.reasoning_level = reasoning_level
        self.auto_repair_enabled = auto_repair_enabled
        self.http_client = http_client
        self.response_audit = response_audit
        self._last_response_meta: dict = {}

    def _client(self, timeout: float | None = None) -> httpx.Client:
        if is_development():
            raise AIClientError("隔离开发模式禁止真实 AI 请求。", code="development_disabled", stage="开发隔离")
        if self.http_client is not None:
            return self.http_client
        return httpx.Client(timeout=httpx.Timeout(timeout or self.request_timeout, connect=self.connect_timeout))

    def _headers(self) -> dict[str, str]:
        if not self.api_key:
            raise AIClientError("未配置 API Key，请先在设置中保存 Key。", code="no_key", stage="读取 Key")
        return {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}

    def discover_models(self) -> list[ModelInfo]:
        client = self._client(self.request_timeout)
        owned = client is not self.http_client
        try:
            response = client.get(f"{self.api_url}/models", headers=self._headers(), timeout=self.request_timeout)
            self._raise_for_status(response, "读取模型列表")
            payload = response.json()
            raw_models = payload.get("data") if isinstance(payload, dict) else None
            if not isinstance(raw_models, list):
                raise AIClientError("模型列表返回格式错误。", code="bad_format", stage="读取模型列表")
            models = []
            for item in raw_models:
                if isinstance(item, dict) and str(item.get("id", "")).strip():
                    model_id = str(item["id"]).strip()
                    models.append(ModelInfo(model_id, str(item.get("name") or model_id), str(item.get("owned_by") or "")))
            if not models:
                raise AIClientError("模型列表为空。", code="empty_models", stage="读取模型列表")
            return models
        except httpx.TimeoutException as error:
            raise AIClientError("读取模型列表连接超时。", code="connect_timeout", stage="读取模型列表") from error
        except httpx.ConnectError as error:
            raise AIClientError("无法连接 DeepSeek，请检查网络或 DNS。", code="network", stage="读取模型列表") from error
        except httpx.HTTPError as error:
            raise AIClientError(f"读取模型列表网络错误：{error}", code="network", stage="读取模型列表") from error
        finally:
            if owned:
                client.close()

    def engineering_chat_stream(self, messages: list[dict[str, str]], cancel_event=None, emit=None) -> dict:
        content, actual_model, usage, first_response, first_text, request_id = self._stream_request(
            messages, max_tokens=8192, cancel_event=cancel_event, emit=emit, json_mode=False,
        )
        return {"content": content, "actual_model": actual_model or self.model_name, "usage": usage,
                "request_id": request_id, "first_response": first_response, "first_text": first_text,
                "response_metrics": dict(self._last_response_meta)}

    def balance(self) -> BalanceInfo:
        client = self._client(self.request_timeout)
        owned = client is not self.http_client
        try:
            response = client.get(f"{self.api_url}/user/balance", headers=self._headers(), timeout=self.request_timeout)
            self._raise_for_status(response, "读取账户信息")
            payload = response.json()
            infos = payload.get("balance_infos", []) if isinstance(payload, dict) else []
            info = infos[0] if infos and isinstance(infos[0], dict) else {}
            return BalanceInfo(
                bool(payload.get("is_available", False)),
                str(info.get("total_balance", "")),
                str(info.get("granted_balance", "")),
                str(info.get("topped_up_balance", "")),
                str(info.get("currency", "CNY")),
                time.strftime("%Y-%m-%d %H:%M:%S"),
            )
        except httpx.TimeoutException as error:
            raise AIClientError("读取账户信息连接超时。", code="connect_timeout", stage="读取账户信息") from error
        except httpx.HTTPError as error:
            raise AIClientError("读取账户信息网络失败。", code="network", stage="读取账户信息") from error
        finally:
            if owned:
                client.close()

    def check_key_network(self) -> DiagnosticResult:
        started = time.perf_counter()
        try:
            models = self.discover_models()
            return DiagnosticResult(True, "读取模型列表", "连接正常；API Key 验证成功；模型列表读取成功。", len(models), time.perf_counter() - started)
        except AIClientError as error:
            return DiagnosticResult(False, error.stage, str(error), 0, time.perf_counter() - started, error.status)

    def test_model(self, cancel_event: Event | None = None, emit: Callable[[StreamEvent], None] | None = None) -> ModelTestResult:
        started = time.perf_counter()
        max_tokens = 1024 if self.thinking_enabled and self.reasoning_level == "max" else 256
        text, actual_model, usage, first_response, first_text, _request_id = self._stream_request(
            [{"role": "user", "content": "请只回复：连接成功"}], max_tokens=max_tokens,
            cancel_event=cancel_event, emit=emit, json_mode=False,
        )
        if not text.strip():
            raise AIClientError(
                "模型测试返回为空。", code="empty_response", stage="测试所选模型",
                metrics={
                    "model": actual_model or self.model_name,
                    "reasoning_level": self.reasoning_level,
                    "thinking_enabled": self.thinking_enabled,
                    "prompt_tokens": usage.get("prompt_tokens", 0),
                    "completion_tokens": usage.get("completion_tokens", 0),
                    "cached_tokens": usage.get("cached_tokens", 0),
                    "first_response_seconds": first_response - started,
                    "first_text_seconds": None,
                    "elapsed_seconds": time.perf_counter() - started,
                    **self._last_response_meta,
                },
            )
        return ModelTestResult(
            self.model_name, actual_model or self.model_name, text.strip(), first_response - started,
            first_text - started if first_text else 0.0, time.perf_counter() - started,
            usage.get("prompt_tokens", 0), usage.get("completion_tokens", 0), usage.get("cached_tokens", 0),
            reasoning_level=self.reasoning_level, thinking_enabled=self.thinking_enabled,
        )

    def suggest_stream(self, description: str, context=None, cancel_event: Event | None = None,
                       task_type: str = "project",
                       emit: Callable[[StreamEvent], None] | None = None) -> AISuggestionResult:
        if not description.strip():
            raise AIClientError("请先输入设备描述。", code="empty_input", stage="检查输入")
        started = time.perf_counter()
        try:
            text, actual_model, usage, first_response, first_text, request_id = self._stream_request(
                build_messages(description, context, task_type), max_tokens=8192,
                cancel_event=cancel_event, emit=emit, json_mode=False,
            )
        except AIClientError as error:
            if error.code == "output_truncated":
                record_id = self._audit_response(
                    str(error.metrics.get("model") or self.model_name),
                    str(error.metrics.get("request_id") or ""),
                    str(error.metrics.get("raw_text") or ""),
                    "输出截断，未进入 JSON 解析", [str(error)], "", metadata=error.metrics,
                )
                if record_id:
                    error.metrics["response_record_id"] = record_id
            raise
        model_generation_seconds = time.perf_counter() - started
        completed_metrics = {
            "model": actual_model or self.model_name,
            "reasoning_level": self.reasoning_level,
            "thinking_enabled": self.thinking_enabled,
            "prompt_tokens": usage.get("prompt_tokens", 0),
            "completion_tokens": usage.get("completion_tokens", 0),
            "cached_tokens": usage.get("cached_tokens", 0),
            "first_response_seconds": first_response - started,
            "first_text_seconds": first_text - started if first_text else None,
            "elapsed_seconds": time.perf_counter() - started,
        }
        completed_metrics.update(self._last_response_meta)
        if not text.strip():
            raise AIClientError("AI 返回为空。", code="empty_response", stage="解析结果", metrics=completed_metrics)
        validation_text = description
        if task_type == "part" and context and context.current_project:
            validation_text += " " + context.current_project["code"]
        parse_started = time.perf_counter()
        repair_used = False
        repair_text = ""
        failed_fields: list[str] = []
        parse_result = "严格 JSON 解析成功"
        try:
            result = parse_suggestions(text, validation_text, usage, task_type)
        except AIResponseError as strict_error:
            failed_fields.append(f"严格解析：{strict_error}")
            if emit:
                emit(StreamEvent("local_repair", "严格解析失败，正在执行本地结构容错修复"))
            try:
                result = parse_suggestions(text, validation_text, usage, task_type, tolerant=True)
                parse_result = "本地结构容错修复成功"
            except AIResponseError as local_error:
                failed_fields.append(f"本地容错：{local_error}")
                if not self.auto_repair_enabled:
                    parse_seconds = time.perf_counter() - parse_started
                    completed_metrics.update({
                        "parse_seconds": parse_seconds,
                        "model_generation_seconds": model_generation_seconds,
                    })
                    record_id = self._audit_response(
                        actual_model or self.model_name, request_id, text,
                        "本地容错失败，自动修复已关闭", failed_fields, "", metadata=completed_metrics,
                    )
                    if record_id:
                        completed_metrics["response_record_id"] = record_id
                    error_type = "json_schema" if local_error.error_type == "schema" else "json_syntax"
                    message = (
                        "AI返回JSON可解析，但内容结构不完整。"
                        if error_type == "json_schema" else "AI返回成功，但JSON语法无效。"
                    )
                    raise AIClientError(message, code=error_type, stage="解析结果", metrics=completed_metrics)
                if emit:
                    emit(StreamEvent("json_repair", "本地容错仍失败，正在执行一次 JSON 修复请求"))
                repair_used = True
                try:
                    repair_text, repair_model, repair_usage, _repair_response, _repair_text_at, _repair_id = self._stream_request(
                        [{"role": "user", "content": (
                            "请将下面结果修复为规定 JSON 结构，不得修改原有项目代号和事实内容。"
                            "必须保留三个 suggestions；单段项目代号允许 segments 为空；只输出 JSON。\n\n" + text
                        )}], max_tokens=2048, cancel_event=cancel_event, emit=emit,
                        thinking_enabled=False,
                    )
                    for key in ("prompt_tokens", "completion_tokens", "cached_tokens"):
                        usage[key] = int(usage.get(key, 0) or 0) + int(repair_usage.get(key, 0) or 0)
                    if repair_model:
                        actual_model = repair_model
                    result = parse_suggestions(repair_text, validation_text, usage, task_type, tolerant=True)
                    parse_result = "一次 JSON 修复请求后解析成功"
                except Exception as repair_error:
                    failed_fields.append(f"JSON修复：{repair_error}")
                    parse_seconds = time.perf_counter() - parse_started
                    completed_metrics.update({
                        "prompt_tokens": usage.get("prompt_tokens", 0),
                        "completion_tokens": usage.get("completion_tokens", 0),
                        "cached_tokens": usage.get("cached_tokens", 0),
                        "parse_seconds": parse_seconds,
                        "model_generation_seconds": model_generation_seconds,
                        "elapsed_seconds": time.perf_counter() - started,
                    })
                    record_id = self._audit_response(
                        actual_model or self.model_name, request_id, text, "三级解析全部失败",
                        failed_fields, repair_text, metadata=completed_metrics,
                    )
                    if record_id:
                        completed_metrics["response_record_id"] = record_id
                    if isinstance(repair_error, AIClientError) and repair_error.code == "output_truncated":
                        completed_metrics.update(repair_error.metrics)
                        completed_metrics["raw_text"] = repair_error.metrics.get("raw_text", repair_text)
                        raise AIClientError(
                            "AI 输出因长度限制被截断，无法可靠解析。", code="output_truncated",
                            stage="生成答案", metrics=completed_metrics,
                        ) from repair_error
                    error_type = "json_schema" if isinstance(repair_error, AIResponseError) and repair_error.error_type == "schema" else "json_syntax"
                    raise AIClientError(
                        ("AI返回JSON可解析，但内容结构不完整：" if error_type == "json_schema" else "AI返回成功，但JSON语法无效：") + str(repair_error)
                        + (f"（原始响应记录：{record_id}）" if record_id else ""),
                        code=error_type, stage="解析结果",
                        metrics=completed_metrics,
                    ) from repair_error
        parse_seconds = time.perf_counter() - parse_started
        completed_metrics["parse_seconds"] = parse_seconds
        record_id = self._audit_response(
            actual_model or self.model_name, request_id, text, parse_result, failed_fields, repair_text,
            metadata=completed_metrics,
        )
        return AISuggestionResult(
            result.suggestions, result.missing_information, result.prompt_tokens, result.completion_tokens,
            result.cached_tokens, self.model_name, actual_model or self.model_name, time.perf_counter() - started,
            first_response - started, first_text - started if first_text else 0.0, self.reasoning_level,
            self.thinking_enabled, {
                "similar_projects": len(context.similar_projects) if context else 0,
                "similar_parts": len(context.similar_parts) if context else 0,
                "feedback": len(context.feedback) if context else 0,
            },
            assess_naming_quality(result.suggestions, context, task_type),
            parse_seconds, model_generation_seconds, repair_used, record_id,
            result.module_recommendations, completed_metrics,
        )

    def _stream_request(self, messages: list[dict[str, str]], *, max_tokens: int,
                        cancel_event: Event | None, emit: Callable[[StreamEvent], None] | None,
                        json_mode: bool = True, thinking_enabled: bool | None = None):
        if cancel_event and cancel_event.is_set():
            raise AICancelled()
        request_thinking = self.thinking_enabled if thinking_enabled is None else thinking_enabled
        started = time.perf_counter()
        first_response = 0.0
        first_text = 0.0
        text_parts: list[str] = []
        usage: dict = {}
        actual_model = ""
        request_id = ""
        finish_reason = ""
        stream_event_count = 0
        content_chunk_count = 0
        reasoning_chunk_count = 0
        stream_assembly_started = time.perf_counter()
        reasoning_started = 0.0
        reasoning_ended = 0.0
        def metrics() -> dict:
            cached = int(usage.get("cached_tokens", 0) or 0)
            prompt = int(usage.get("prompt_tokens", 0) or 0)
            return {
                "model": actual_model or self.model_name,
                "reasoning_level": self.reasoning_level,
                "thinking_enabled": request_thinking,
                "prompt_tokens": prompt,
                "completion_tokens": int(usage.get("completion_tokens", 0) or 0),
                "cached_tokens": cached,
                "cache_miss_tokens": max(0, prompt - cached),
                "first_response_seconds": first_response - started if first_response else None,
                "first_text_seconds": first_text - started if first_text else None,
                "elapsed_seconds": time.perf_counter() - started,
                "finish_reason": finish_reason or "暂无数据",
                "stream_event_count": stream_event_count,
                "content_chunk_count": content_chunk_count,
                "reasoning_chunk_count": reasoning_chunk_count,
                "final_content_length": len("".join(text_parts)),
                "request_id": request_id or "暂无数据",
                "stream_assembly_seconds": time.perf_counter() - stream_assembly_started,
                "server_accept_seconds": first_response - started if first_response else None,
                "reasoning_seconds": reasoning_ended - reasoning_started if reasoning_started and reasoning_ended else None,
            }
        payload = {
            "model": self.model_name, "messages": messages, "stream": True,
            "temperature": 0.2, "max_tokens": max_tokens,
            "stream_options": {"include_usage": True},
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        # DeepSeek defaults to thinking when the toggle is omitted.
        payload["thinking"] = {"type": "enabled" if request_thinking else "disabled"}
        if request_thinking:
            payload["reasoning_effort"] = self.reasoning_level
        client = self._client(self.generation_timeout)
        owned = client is not self.http_client
        cancel_callbacks = []

        def register_cancel(callback):
            if cancel_event is not None and hasattr(cancel_event, "add_callback"):
                cancel_event.add_callback(callback)
                cancel_callbacks.append(callback)

        try:
            if owned:
                register_cancel(client.close)
            if emit:
                emit(StreamEvent("submitted", "请求已提交", data=metrics()))
            with client.stream("POST", f"{self.api_url}/chat/completions", headers=self._headers(), json=payload,
                               timeout=httpx.Timeout(self.generation_timeout, connect=self.connect_timeout)) as response:
                register_cancel(response.close)
                first_response = time.perf_counter()
                request_id = response.headers.get("x-request-id") or response.headers.get("request-id") or ""
                self._raise_for_status(response, "连接 DeepSeek")
                if emit:
                    emit(StreamEvent("accepted", "服务器已接受请求", data=metrics()))
                    if request_thinking:
                        emit(StreamEvent("thinking", f"{self.model_name} 正在思考", data=metrics()))
                for line in response.iter_lines():
                    if cancel_event and cancel_event.is_set():
                        raise AICancelled()
                    if time.perf_counter() - started > self.generation_timeout:
                        raise AIClientError("AI 生成超过设置的总超时。", code="generation_timeout", stage="生成答案", metrics=metrics())
                    if not line or not line.startswith("data:"):
                        continue
                    raw = line[5:].strip()
                    if raw == "[DONE]":
                        break
                    try:
                        event = json.loads(raw)
                    except json.JSONDecodeError:
                        continue
                    stream_event_count += 1
                    request_id = str(event.get("id") or request_id)
                    actual_model = str(event.get("model") or actual_model)
                    choice = (event.get("choices") or [{}])[0]
                    finish_reason = str(choice.get("finish_reason") or finish_reason)
                    delta = choice.get("delta") or {}
                    reasoning_chunk = delta.get("reasoning_content") or delta.get("reasoning") or ""
                    if reasoning_chunk:
                        if not reasoning_started:
                            reasoning_started = time.perf_counter()
                        reasoning_chunk_count += 1
                        reasoning_ended = time.perf_counter()
                    chunk = delta.get("content") or ""
                    if chunk:
                        content_chunk_count += 1
                        if not first_text:
                            first_text = time.perf_counter()
                            if emit:
                                emit(StreamEvent("first_text", "收到首个文字", data=metrics()))
                        text_parts.append(str(chunk))
                        if emit:
                            emit(StreamEvent("generating", "正在生成建议", str(chunk)))
                    if isinstance(event.get("usage"), dict):
                        usage.update(event["usage"])
                details = usage.get("prompt_tokens_details") or {}
                usage["cached_tokens"] = int(details.get("cached_tokens", 0) or usage.get("cached_tokens", 0) or 0)
                if emit:
                    emit(StreamEvent("usage", "Token 使用信息已读取", data=metrics()))
                if finish_reason in {"length", "max_tokens", "incomplete"}:
                    truncated_metrics = metrics()
                    truncated_metrics["raw_text"] = "".join(text_parts)
                    if emit:
                        emit(StreamEvent("output_truncated", "AI 输出因长度限制被截断，无法可靠解析。", data=truncated_metrics))
                    raise AIClientError(
                        "AI 输出因长度限制被截断，无法可靠解析。",
                        code="output_truncated", stage="生成答案", metrics=truncated_metrics,
                    )
                if emit:
                    emit(StreamEvent("completed", "AI 返回完成", data=metrics()))
            return "".join(text_parts), actual_model, usage, first_response or time.perf_counter(), first_text, request_id
        except AICancelled as error:
            error.metrics.update(metrics())
            if emit:
                emit(StreamEvent("cancelled", "用户取消", data=error.metrics))
            raise
        except AIClientError as error:
            error.metrics.update(metrics())
            raise
        except httpx.TimeoutException as error:
            if cancel_event and cancel_event.is_set():
                raise AICancelled(metrics()) from error
            code = "generation_timeout" if first_response else "connect_timeout"
            message = "DeepSeek 已接受请求，但模型长时间没有返回结果。" if first_response else "连接 DeepSeek 超时。"
            if emit:
                emit(StreamEvent("timeout", message))
            raise AIClientError(message, code=code, stage="生成答案", metrics=metrics()) from error
        except httpx.HTTPStatusError as error:
            status = error.response.status_code
            code = "invalid_key" if status in (401, 403) else "rate_limit" if status == 429 else "model_unavailable" if status in (400, 404) else "service_error"
            raise AIClientError(f"DeepSeek 服务异常（HTTP {status}）。", code=code, stage="连接 DeepSeek", status=status, metrics=metrics()) from error
        except httpx.ConnectError as error:
            raise AIClientError("无法连接 DeepSeek，请检查网络、DNS 或代理。", code="network", stage="建立网络连接", metrics=metrics()) from error
        except httpx.HTTPError as error:
            if cancel_event and cancel_event.is_set():
                raise AICancelled(metrics()) from error
            raise AIClientError(f"流式响应中断：{error}", code="stream_interrupted", stage="生成答案", metrics=metrics()) from error
        except (RuntimeError, OSError) as error:
            if cancel_event and cancel_event.is_set():
                raise AICancelled(metrics()) from error
            raise
        finally:
            for callback in cancel_callbacks:
                cancel_event.remove_callback(callback)
            if owned:
                client.close()

    def _audit_response(self, model: str, request_id: str, raw_text: str, parse_result: str,
                        failed_fields: list[str], repair_result: str, metadata: dict | None = None) -> str:
        if self.response_audit is None:
            return ""
        try:
            return self.response_audit.append(
                model=model, request_id=request_id, raw_text=raw_text, parse_result=parse_result,
                failed_fields=failed_fields, repair_result=repair_result, metadata=metadata,
            )
        except OSError:
            return ""

    @staticmethod
    def _raise_for_status(response: httpx.Response, stage: str) -> None:
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as error:
            status = response.status_code
            code = "invalid_key" if status in (401, 403) else "rate_limit" if status == 429 else "model_unavailable" if status in (400, 404) and "模型" in stage else "service_error"
            raise AIClientError(f"{stage}失败（HTTP {status}）。", code=code, stage=stage, status=status) from error
