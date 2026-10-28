# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 zhang18166

"""Opt-in local development profile; configure before constructing Qt or stores."""
import argparse
import hashlib
import json
import os
import tempfile
from pathlib import Path

DEV_ROOT_ENV = "MECHANICAL_PROJECT_NAMER_DEV_ROOT"
MARKER = ".mechanical-workbench-development.json"
IDENTITY = {"kind": "MechanicalWorkbenchDevelopment", "format": 1}


def is_development() -> bool:
    return bool(os.environ.get(DEV_ROOT_ENV))


def development_root() -> Path:
    return Path(os.environ[DEV_ROOT_ENV])


def configure_development(arguments: list[str]) -> tuple[list[str], bool]:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--dev", action="store_true")
    parser.add_argument("--dev-root", type=Path)
    parser.add_argument("--dev-smoke", action="store_true")
    options, remaining = parser.parse_known_args(arguments)
    if not options.dev:
        if options.dev_root is not None or options.dev_smoke:
            parser.error("--dev-root / --dev-smoke 必须与 --dev 一起使用")
        return remaining, False
    local = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData/Local"))
    roaming = Path(os.environ.get("APPDATA", Path.home() / "AppData/Roaming"))
    root = (options.dev_root or local / "MechanicalWorkbenchDev" / "phase0").resolve()
    protected = [local / "MechanicalProjectNamer", roaming / "MechanicalProjectNamer",
                 Path(__file__).resolve().parents[1]]
    for folder in protected:
        folder = folder.resolve()
        if root.is_relative_to(folder) or folder.is_relative_to(root):
            raise ValueError("开发目录不能与正式数据目录或源码仓库重叠。")
    marker = root / MARKER
    if root.exists():
        if not root.is_dir():
            raise ValueError("开发目录必须是文件夹。")
        if marker.exists():
            if marker.is_symlink() or json.loads(marker.read_text(encoding="utf-8")) != IDENTITY:
                raise ValueError("开发目录标识不匹配。")
        elif any(root.iterdir()):
            raise ValueError("拒绝接管非空目录；请选择新的开发专用目录。")
        for current, directories, files in os.walk(root, followlinks=False):
            for name in directories + files:
                path = Path(current) / name
                if path.is_symlink() or path.is_junction():
                    raise ValueError("开发目录中不允许重定向链接。")
    root.mkdir(parents=True, exist_ok=True)
    marker.write_text(json.dumps(IDENTITY), encoding="utf-8")
    for name in ("local", "roaming", "cache/tmp", "workspace"):
        folder = root / name
        # Do not follow a substituted directory into real files on subsequent runs.
        if folder.resolve() != folder:
            raise ValueError("开发目录中不允许重定向链接。")
        folder.mkdir(parents=True, exist_ok=True)
    os.environ[DEV_ROOT_ENV] = str(root)
    os.environ["LOCALAPPDATA"] = str(root / "local")
    os.environ["APPDATA"] = str(root / "roaming")
    os.environ["TEMP"] = os.environ["TMP"] = str(root / "cache/tmp")
    tempfile.tempdir = None
    token = hashlib.sha256(str(root).casefold().encode("utf-8")).hexdigest()[:20]
    os.environ["MECHANICAL_PROJECT_NAMER_INSTANCE_NAME"] = "MechanicalWorkbench.Dev." + token
    os.environ.pop("MECHANICAL_PROJECT_NAMER_SMOKE_CHECKS", None)
    os.environ.pop("MECHANICAL_PROJECT_NAMER_SMOKE_QUIT_MS", None)
    return remaining, options.dev_smoke


def seed_demo(repository, workflow, settings) -> None:
    """Idempotent fictional data, never copied from the real database."""
    from .numbering.simple import SIMPLE_TYPE
    from .projects import ProjectService
    projects = repository.list_projects(project_status="all")
    if not projects:
        folder = development_root() / "workspace" / "DEMO"
        folder.mkdir(parents=True, exist_ok=True)
        project = ProjectService(repository).create(
            "演示设备（虚构）", "仅用于隔离开发验证", "DEMO", directory=str(folder),
            modules=[("DJ", "演示模块")],
        )
        workflow.save_named_and_advance(project.id, "DJ", SIMPLE_TYPE, "演示连接块")
        settings.current_project_id = project.id
        settings.current_module_code = "DJ"
    settings.solidworks_enabled = False
