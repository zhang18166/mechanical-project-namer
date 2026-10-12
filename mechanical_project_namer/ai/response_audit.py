# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 zhang18166

import json
import uuid
from datetime import datetime
from pathlib import Path

from .runtime import sanitize_log_message


class AIResponseAudit:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    def append(self, *, model: str, request_id: str, raw_text: str, parse_result: str,
               failed_fields: list[str] | None = None, repair_result: str = "",
               metadata: dict | None = None) -> str:
        record_id = uuid.uuid4().hex
        record = {
            "record_id": record_id,
            "returned_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "model": model,
            "request_id": request_id or "暂无数据",
            "raw_text": sanitize_log_message(raw_text),
            "parse_result": parse_result,
            "failed_fields": failed_fields or [],
            "repair_result": sanitize_log_message(repair_result),
        }
        if metadata:
            record["metadata"] = {
                str(key): value for key, value in metadata.items()
                if key not in {"api_key", "authorization", "raw_text"}
            }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8", newline="\n") as stream:
            stream.write(json.dumps(record, ensure_ascii=False) + "\n")
        return record_id

    def latest(self, limit: int = 20) -> list[dict]:
        try:
            lines = self.path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return []
        records = []
        for line in reversed(lines):
            try:
                item = json.loads(line)
            except (ValueError, TypeError):
                continue
            if isinstance(item, dict):
                records.append(item)
            if len(records) >= limit:
                break
        return records
