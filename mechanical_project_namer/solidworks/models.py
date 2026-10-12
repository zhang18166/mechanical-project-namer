# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 zhang18166

from dataclasses import asdict, dataclass, field
from pathlib import Path


@dataclass(slots=True)
class PendingSolidWorksCreateContext:
    operation_id: str
    project_id: int
    module_code: str
    part_category: str
    part_sequence: int
    part_name: str
    extra_notes: str = ""
    final_name: str = ""
    original_action: str = "create_part"
    created_at: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(slots=True)
class SolidWorksInfo:
    connected: bool = False
    process_id: int | None = None
    process_count: int = 0
    revision: str = ""
    build: str = ""
    year: int | None = None
    service_pack: str = ""
    supported: bool = False
    support_level: str = "unknown"
    template_path: str = ""
    template_source: str = ""
    assistant_integrity: str = "unknown"
    solidworks_integrity: str = "unknown"

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(slots=True)
class OperationRequest:
    operation_id: str
    action: str = "create_part"
    target_path: str = ""
    fallback_template: str = ""
    allow_unsupported_version: bool = False
    activate_document: bool = True
    bring_to_front: bool = True
    result_path: str = ""

    @classmethod
    def from_dict(cls, value: dict) -> "OperationRequest":
        allowed = cls.__dataclass_fields__
        return cls(**{key: item for key, item in value.items() if key in allowed})

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(slots=True)
class OperationResult:
    operation_id: str
    success: bool
    stage: str
    message: str
    info: SolidWorksInfo = field(default_factory=SolidWorksInfo)
    target_path: str = ""
    template_path: str = ""
    file_size: int = 0
    document_path: str = ""
    original_document_title: str = ""
    original_document_path: str = ""
    original_document_count: int = 0
    original_document_preserved: bool = False
    save_error: int = 0
    save_warning: int = 0
    error_code: str = ""
    timings: dict[str, float] = field(default_factory=dict)
    logs: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        value = asdict(self)
        value["info"] = self.info.to_dict()
        return value

    @classmethod
    def from_dict(cls, value: dict) -> "OperationResult":
        allowed = cls.__dataclass_fields__
        data = {key: item for key, item in value.items() if key in allowed}
        data["info"] = SolidWorksInfo(**data.get("info", {}))
        return cls(**data)

    @property
    def verified_file(self) -> bool:
        return self.success and self.file_size > 0 and Path(self.target_path).is_file()
