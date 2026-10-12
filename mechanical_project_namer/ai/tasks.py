# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 zhang18166

from dataclasses import dataclass
from enum import StrEnum
from time import perf_counter
from typing import Any
from uuid import uuid4


class AITaskType(StrEnum):
    AI_PLANNING = "AI_PLANNING"
    AI_CHAT = "AI_CHAT"
    MODEL_TEST = "MODEL_TEST"
    MEMORY_SUMMARY = "MEMORY_SUMMARY"
    JSON_REPAIR = "JSON_REPAIR"


@dataclass
class AITaskState:
    task_type: AITaskType
    owner_view: str
    worker: Any
    thread: Any
    task_id: str = ""
    started_at: float = 0.0
    status: str = "running"

    def __post_init__(self) -> None:
        self.task_id = self.task_id or str(uuid4())
        self.started_at = self.started_at or perf_counter()

    @property
    def cancel_token(self):
        return getattr(self.worker, "cancel_event", None)

    def cancel(self) -> None:
        if self.status == "running":
            self.status = "cancelling"
            self.worker.cancel()
