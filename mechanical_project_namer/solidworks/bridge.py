# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 zhang18166

import os
import time
from pathlib import Path

from .compatibility import parse_revision, require_supported, support_level
from .connection import (
    bring_process_to_front, connect_active_application, process_file_version,
    process_integrity_level, require_single_process,
)
from .constants import (
    PART_TEMPLATE_EXTENSIONS, SW_DEFAULT_PAPER_SIZE, SW_DEFAULT_TEMPLATE_PART,
    SW_DONT_REBUILD_ACTIVE_DOC, SW_SAVE_AS_CURRENT_VERSION, SW_SAVE_AS_OPTIONS_SILENT,
)
from .errors import SolidWorksError
from .models import OperationRequest, OperationResult, SolidWorksInfo
from .validation import validate_target_path


def _call_or_value(obj, name: str, default=""):
    value = getattr(obj, name, default)
    try:
        return value() if callable(value) and not hasattr(value, "_oleobj_") else value
    except Exception:
        return default


def _document_count(app) -> int:
    count = _call_or_value(app, "GetDocumentCount", None)
    if count is not None:
        return int(count)
    documents = _call_or_value(app, "GetDocuments", ()) or ()
    return len(documents)


def _resolve_template(app, fallback: str) -> tuple[Path, str]:
    try:
        configured = str(app.GetUserPreferenceStringValue(SW_DEFAULT_TEMPLATE_PART) or "").strip()
    except Exception:
        configured = ""
    candidates = ((configured, "solidworks_default"), (str(fallback or "").strip(), "fallback"))
    missing_default = ""
    for raw, source in candidates:
        if not raw:
            continue
        path = Path(raw)
        if source == "solidworks_default" and not path.is_file():
            missing_default = str(path)
            continue
        if path.suffix.casefold() not in PART_TEMPLATE_EXTENSIONS:
            if source == "fallback":
                raise SolidWorksError("template_invalid", "备用零件模板扩展名必须为 .prtdot。", "template")
            continue
        if path.is_file() and os.access(path, os.R_OK):
            return path, source
    if missing_default:
        message = f"SOLIDWORKS默认零件模板不存在：\n{missing_default}\n\n请配置默认模板或选择备用模板。"
        code = "default_template_missing"
    else:
        message = "SOLIDWORKS默认零件模板未配置，且没有可用的备用模板。"
        code = "template_not_configured"
    raise SolidWorksError(code, message, "template")


def _save_document(document, target: Path) -> tuple[bool, int, int]:
    extension = _call_or_value(document, "Extension", None)
    if extension is not None:
        try:
            import pythoncom
            from win32com.client import VARIANT

            export_data = VARIANT(pythoncom.VT_DISPATCH, None)
            errors = VARIANT(pythoncom.VT_BYREF | pythoncom.VT_I4, 0)
            warnings = VARIANT(pythoncom.VT_BYREF | pythoncom.VT_I4, 0)
            saved = bool(extension.SaveAs(
                str(target), SW_SAVE_AS_CURRENT_VERSION, SW_SAVE_AS_OPTIONS_SILENT,
                export_data, errors, warnings,
            ))
            return saved, int(errors.value), int(warnings.value)
        except (AttributeError, TypeError):
            pass
    return bool(document.SaveAs3(str(target), SW_SAVE_AS_CURRENT_VERSION, SW_SAVE_AS_OPTIONS_SILENT)), 0, 0


def _activate_document(app, title: str):
    import pythoncom
    from win32com.client import VARIANT

    errors = VARIANT(pythoncom.VT_BYREF | pythoncom.VT_I4, 0)
    try:
        document = app.ActivateDoc3(title, False, SW_DONT_REBUILD_ACTIVE_DOC, errors)
    except AttributeError:
        document = app.ActivateDoc2(title, False, errors)
    return document, int(errors.value)


class SolidWorksBridge:
    def __init__(self, process_provider=require_single_process, connector=connect_active_application) -> None:
        self.process_provider = process_provider
        self.connector = connector

    def probe(self, fallback_template: str = "", allow_unsupported: bool = False) -> SolidWorksInfo:
        process = self.process_provider()
        app = self.connector()
        revision = str(_call_or_value(app, "RevisionNumber", ""))
        year, service_pack = parse_revision(revision)
        supported, level = support_level(year)
        require_supported(year, allow_unsupported)
        build = str(_call_or_value(app, "GetBuildNumbers", "")) or process_file_version(process.executable)
        template, source = _resolve_template(app, fallback_template)
        return SolidWorksInfo(
            connected=True, process_id=process.process_id, process_count=1,
            revision=revision, build=build, year=year, service_pack=service_pack,
            supported=supported, support_level=level, template_path=str(template), template_source=source,
            assistant_integrity=process_integrity_level(), solidworks_integrity=process_integrity_level(process.process_id),
        )

    def execute(self, request: OperationRequest, progress=lambda _stage, _message: None) -> OperationResult:
        started = time.perf_counter()
        logs: list[str] = []

        def emit(stage: str, message: str) -> None:
            logs.append(message)
            progress(stage, message)

        emit("process", "正在检测SOLIDWORKS进程")
        process = self.process_provider()
        emit("connection", "正在建立COM连接")
        app = self.connector()
        revision = str(_call_or_value(app, "RevisionNumber", ""))
        year, service_pack = parse_revision(revision)
        require_supported(year, request.allow_unsupported_version)
        supported, level = support_level(year)
        build = str(_call_or_value(app, "GetBuildNumbers", "")) or process_file_version(process.executable)
        emit("connected", f"已连接SOLIDWORKS {year} {service_pack}".strip())
        template, source = _resolve_template(app, request.fallback_template)
        info = SolidWorksInfo(
            connected=True, process_id=process.process_id, process_count=1,
            revision=revision, build=build, year=year, service_pack=service_pack,
            supported=supported, support_level=level, template_path=str(template), template_source=source,
            assistant_integrity=process_integrity_level(), solidworks_integrity=process_integrity_level(process.process_id),
        )
        emit("template_resolved", f"零件模板：{template}")
        if request.action == "probe":
            return OperationResult(request.operation_id, True, "completed", "SOLIDWORKS连接测试成功。", info=info, template_path=str(template), logs=logs)

        target = validate_target_path(request.target_path)
        active_before = _call_or_value(app, "ActiveDoc", None)
        original_title = str(_call_or_value(active_before, "GetTitle", "")) if active_before else ""
        original_path = str(_call_or_value(active_before, "GetPathName", "")) if active_before else ""
        original_count = _document_count(app)
        emit("document_created", "正在创建新的空白零件")
        new_document = app.NewDocument(str(template), SW_DEFAULT_PAPER_SIZE, 0.0, 0.0)
        if new_document is None:
            raise SolidWorksError("document_create_failed", "SOLIDWORKS未能创建新的空白零件。", "document_created")
        emit("saving", f"正在保存：{target}")
        try:
            saved, save_error, save_warning = _save_document(new_document, target)
        except Exception as error:
            raise SolidWorksError("save_failed", "SOLIDWORKS保存调用失败。", "file_saved", details={"exception": type(error).__name__}) from error
        if not saved or save_error:
            raise SolidWorksError(
                "save_failed", "SOLIDWORKS返回保存失败。", "file_saved",
                details={"save_error": save_error, "save_warning": save_warning},
            )
        if save_warning:
            raise SolidWorksError(
                "save_warning", "SOLIDWORKS保存完成，但返回警告；已停止数据库提交。", "file_saved",
                details={"save_error": save_error, "save_warning": save_warning},
            )
        if not target.is_file():
            raise SolidWorksError("file_missing", "SOLIDWORKS返回成功，但目标文件没有实际生成。", "verification")
        size = target.stat().st_size
        if size <= 0:
            raise SolidWorksError("file_empty", "SOLIDWORKS零件文件大小为0。", "verification")
        document_path = str(_call_or_value(new_document, "GetPathName", ""))
        if Path(document_path).resolve(strict=False) != target.resolve(strict=False):
            raise SolidWorksError("document_path_mismatch", "新文档路径与目标路径不一致。", "verification", details={"document_path": document_path})
        if request.activate_document:
            active_document = _call_or_value(app, "ActiveDoc", None)
            active_path = str(_call_or_value(active_document, "GetPathName", "")) if active_document else ""
            if Path(active_path).resolve(strict=False) != target.resolve(strict=False):
                title = str(_call_or_value(new_document, "GetTitle", target.name))
                try:
                    activated, activation_error = _activate_document(app, title)
                    activated_path = str(_call_or_value(activated, "GetPathName", "")) if activated else ""
                    if activation_error or Path(activated_path).resolve(strict=False) != target.resolve(strict=False):
                        raise RuntimeError(f"ActivateDoc error {activation_error}, path {activated_path}")
                except Exception as error:
                    raise SolidWorksError("activation_failed", "零件已保存，但SOLIDWORKS无法激活新文档。", "activation") from error
        if request.bring_to_front:
            bring_process_to_front(process.process_id)
        original_preserved = True
        if active_before is not None:
            original_preserved = (
                str(_call_or_value(active_before, "GetTitle", "")) == original_title
                and str(_call_or_value(active_before, "GetPathName", "")) == original_path
                and _document_count(app) >= original_count
            )
        emit("file_saved", f"文件验证成功，大小 {size} 字节")
        return OperationResult(
            request.operation_id, True, "file_saved", "SOLIDWORKS零件已创建并验证。",
            info=info, target_path=str(target), template_path=str(template), file_size=size,
            document_path=document_path, original_document_title=original_title,
            original_document_path=original_path, original_document_count=original_count,
            original_document_preserved=original_preserved,
            save_error=save_error, save_warning=save_warning,
            timings={"total": round(time.perf_counter() - started, 3)}, logs=logs,
        )
