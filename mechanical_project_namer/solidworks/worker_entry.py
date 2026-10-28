# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 zhang18166

import json
import sys
import time
from datetime import datetime
from pathlib import Path

from .bridge import SolidWorksBridge
from .errors import SolidWorksError
from .models import OperationRequest, OperationResult
from ..development import is_development


def _write_json(path: str, payload: dict) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(target.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(target)


def _emit_stdout(payload: dict) -> None:
    if sys.stdout is not None:
        print(json.dumps(payload, ensure_ascii=False), flush=True)


def _configure_utf8_stdio() -> None:
    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            reconfigure(encoding="utf-8", errors="replace")


def run_worker(request_file: str, bridge: SolidWorksBridge | None = None) -> int:
    if is_development():
        raise SolidWorksError("development_disabled", "隔离开发模式禁止运行 SOLIDWORKS Worker。", "worker")
    _configure_utf8_stdio()
    raw = json.loads(Path(request_file).read_text(encoding="utf-8"))
    request = OperationRequest.from_dict(raw)
    bridge = bridge or SolidWorksBridge()
    started = time.perf_counter()

    def progress(stage: str, message: str) -> None:
        event = {
            "type": "progress",
            "operation_id": request.operation_id,
            "stage": stage,
            "message": message,
            "timestamp": datetime.now().astimezone().isoformat(timespec="milliseconds"),
            "data": {},
        }
        _emit_stdout(event)

    try:
        import pythoncom

        pythoncom.CoInitializeEx(pythoncom.COINIT_APARTMENTTHREADED)
        try:
            result = bridge.execute(request, progress)
        finally:
            pythoncom.CoUninitialize()
        payload = result.to_dict()
        exit_code = 0
    except SolidWorksError as error:
        payload = OperationResult(
            request.operation_id, False, error.stage, str(error), target_path=request.target_path,
            error_code=error.code, timings={"total": round(time.perf_counter() - started, 3)},
        ).to_dict()
        payload["error_details"] = error.details
        exit_code = 2
    except Exception as error:
        payload = OperationResult(
            request.operation_id, False, "worker", f"SOLIDWORKS Worker异常：{error}",
            target_path=request.target_path, error_code="worker_exception",
            timings={"total": round(time.perf_counter() - started, 3)},
        ).to_dict()
        exit_code = 3
    if request.result_path:
        _write_json(request.result_path, payload)
    _emit_stdout({"type": "result", "result": payload})
    return exit_code


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 2 or args[0] != "--solidworks-worker":
        return 64
    return run_worker(args[1])


if __name__ == "__main__":
    raise SystemExit(main())
