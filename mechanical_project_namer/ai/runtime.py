# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 zhang18166

import re


KEY_PATTERN = re.compile(r"\bsk-[A-Za-z0-9_-]{6,}\b", re.IGNORECASE)
AUTH_PATTERN = re.compile(r"Authorization\s*:\s*Bearer\s+\S+", re.IGNORECASE)


def sanitize_log_message(message: str) -> str:
    value = AUTH_PATTERN.sub("Authorization: [已隐藏]", str(message))
    return KEY_PATTERN.sub("sk-[已隐藏]", value)

