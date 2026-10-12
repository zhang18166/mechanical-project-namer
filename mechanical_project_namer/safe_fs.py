# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 zhang18166

"""清理类文件删除的安全包装。

**为什么需要这个模块（2026-09-16 阶段二 A 复盘）**

生产代码里有若干"善后删除"：删掉已消费的 SW 恢复文件、删掉 worker 的 request/result
临时 json、删掉目录可写性探测文件、删掉用户确认的测试零件。这些删除**全部属于尽力而为的
清理动作**，与业务结果无关。但如果它们抛异常，后果非常严重：

- `commit_created_file()` 里删除恢复文件失败 → 零件**已经建好、记录已经提交**，却向调用方
  抛异常，UI 会告诉用户"创建失败"（假失败，用户可能重复创建）。
- `reconcile_pending()` 里删除恢复文件失败 → 整个启动恢复流程中断，后续待恢复操作的编号
  继续处于锁定状态。

运行环境（本机 WorkBuddy 宿主注入的“安全删除”包装）会在删除量超阈值时抛 `SystemExit`。
`SystemExit` 不是 `Exception` 的子类，所以 `except Exception` / `except OSError` 都拦不住它。
而**删不掉一个善后文件本身不会破坏任何数据**，此时正确行为是：记 warning、保留文件、
继续主流程（遗留文件在下次启动会被幂等重放，见 `SolidWorksService._commit` 的
"status == completed 直接返回已有记录" 分支）。

约定：**生产代码中所有清理类删除必须走 `remove_file()`。**
业务上"必须删掉否则算失败"的删除（目前不存在）不要用本函数。

`KeyboardInterrupt` / `GeneratorExit` 仍按原样向上传播——用户主动中断不该被吞掉。
"""
from __future__ import annotations

import logging
from pathlib import Path

_LOGGER = logging.getLogger(__name__)


def remove_file(path, *, what: str = "临时文件", logger: logging.Logger | None = None) -> bool:
    """尽力删除单个文件。返回是否已确认不存在（含本来就存在的情况）。

    绝不因删除失败而抛异常；失败时返回 False 并记录 warning。
    """
    target = Path(path)
    log = logger or _LOGGER
    try:
        target.unlink()
        return True
    except FileNotFoundError:
        return True
    except BaseException as error:  # noqa: BLE001 - 见模块 docstring
        if isinstance(error, (KeyboardInterrupt, GeneratorExit)):
            raise
        log.warning(
            "%s清理失败（不影响主流程；文件保留，下次启动会幂等重放）：%s → %s",
            what, target, error,
        )
        return False
