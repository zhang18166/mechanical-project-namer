# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 zhang18166

import logging

from keyring.backends.Windows import WinVaultKeyring


LOGGER = logging.getLogger(__name__)
SERVICE_NAME = "MechanicalProjectNamer.DeepSeek"
ACCOUNT_NAME = "api-key"


class CredentialStoreError(RuntimeError):
    pass


class CredentialStore:
    def __init__(self, backend=None) -> None:
        self.backend = backend or WinVaultKeyring()

    def get_api_key(self) -> str | None:
        try:
            value = self.backend.get_password(SERVICE_NAME, ACCOUNT_NAME)
            return value.strip() if value and value.strip() else None
        except Exception as error:
            LOGGER.exception("读取 Windows 凭据失败")
            raise CredentialStoreError("无法读取 Windows 凭据管理器中的 API Key。") from error

    def set_api_key(self, api_key: str) -> None:
        value = api_key.strip()
        if not value:
            raise CredentialStoreError("API Key 不能为空。")
        try:
            self.backend.set_password(SERVICE_NAME, ACCOUNT_NAME, value)
        except Exception as error:
            LOGGER.exception("写入 Windows 凭据失败")
            raise CredentialStoreError("无法将 API Key 保存到 Windows 凭据管理器。") from error

    def delete_api_key(self) -> None:
        try:
            self.backend.delete_password(SERVICE_NAME, ACCOUNT_NAME)
        except Exception as error:
            if error.__class__.__name__ == "PasswordDeleteError":
                return
            LOGGER.exception("删除 Windows 凭据失败")
            raise CredentialStoreError("无法删除 Windows 凭据管理器中的 API Key。") from error

    def is_configured(self) -> bool:
        return self.get_api_key() is not None

    def masked_status(self) -> str:
        value = self.get_api_key()
        if not value:
            return "未配置 API Key"
        suffix = value[-4:] if len(value) >= 4 else value
        return f"已安全保存  ·  sk-••••••••{suffix}"
