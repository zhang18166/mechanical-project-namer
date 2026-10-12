# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 zhang18166

class SolidWorksError(RuntimeError):
    def __init__(self, code: str, message: str, stage: str, *, details: dict | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.stage = stage
        self.details = details or {}

    def to_dict(self) -> dict:
        return {
            "code": self.code,
            "message": str(self),
            "stage": self.stage,
            "details": self.details,
        }
