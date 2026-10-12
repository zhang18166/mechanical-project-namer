# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 zhang18166

"""SOLIDWORKS API constants used by the late-bound compatibility layer."""

# swUserPreferenceStringValue_e.swDefaultTemplatePart, SOLIDWORKS API Help.
SW_DEFAULT_TEMPLATE_PART = 8
# swDwgPaperSizes_e.swDwgPaperAsize; ignored for part templates by NewDocument.
SW_DEFAULT_PAPER_SIZE = 0
# swSaveAsVersion_e.swSaveAsCurrentVersion.
SW_SAVE_AS_CURRENT_VERSION = 0
# swSaveAsOptions_e.swSaveAsOptions_Silent.
SW_SAVE_AS_OPTIONS_SILENT = 1
# swRebuildOnActivation_e.swDontRebuildActiveDoc.
SW_DONT_REBUILD_ACTIVE_DOC = 0

SUPPORTED_YEARS = frozenset({2025, 2026})
MIN_SUPPORTED_YEAR = 2025
PART_TEMPLATE_EXTENSIONS = frozenset({".prtdot"})
PART_EXTENSION = ".SLDPRT"

CONNECT_TIMEOUT_SECONDS = 15
TEMPLATE_TIMEOUT_SECONDS = 10
CREATE_TIMEOUT_SECONDS = 30
SAVE_TIMEOUT_SECONDS = 60
OVERALL_TIMEOUT_SECONDS = 120

# Conservative classic Win32 limit. Long-path policy varies across installations.
MAX_FILENAME_CHARS = 255
MAX_PATH_CHARS = 259
