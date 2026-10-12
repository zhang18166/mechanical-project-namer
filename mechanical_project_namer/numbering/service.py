# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 zhang18166

class NumberingError(ValueError):
    pass


def validate_number(number: int) -> int:
    if not 1 <= number <= 999:
        raise NumberingError("图号必须在 001 到 999 之间。")
    return number


def format_filename(project_code: str, drawing_number: int) -> str:
    validate_number(drawing_number)
    return f"{project_code}-{drawing_number:03d}"

