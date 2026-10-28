# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 zhang18166

"""Release layout, SQLite backup, and transactional package switching helpers."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import sqlite3
import subprocess
import sys
import time
from contextlib import closing
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RELEASE = ROOT / "release"


def project_version() -> str:
    """单一版本来源：mechanical_project_namer/__init__.py，且必须与 pyproject.toml 一致。"""
    text = (ROOT / "mechanical_project_namer" / "__init__.py").read_text(encoding="utf-8")
    match = re.search(r'__version__\s*=\s*"([^"]+)"', text)
    if not match:
        raise RuntimeError("无法从 mechanical_project_namer/__init__.py 读取 __version__")
    version = match.group(1)
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    declared = re.search(r'^version\s*=\s*"([^"]+)"', pyproject, re.MULTILINE)
    if declared and declared.group(1) != version:
        raise RuntimeError(
            f"版本号不一致：__init__.py={version} pyproject.toml={declared.group(1)}"
        )
    return version


def git_output(*args: str) -> str:
    try:
        return subprocess.check_output(["git", *args], cwd=ROOT, text=True, stderr=subprocess.DEVNULL).strip()
    except (OSError, subprocess.CalledProcessError):
        return ""


def worktree_dirty() -> bool:
    return bool(git_output("status", "--porcelain"))


def head_tags() -> list[str]:
    return [item for item in git_output("tag", "--points-at", "HEAD").splitlines() if item]


def guard_clean_worktree(*, expected_tag: str = "") -> dict:
    """强制 commit → tag → build → release：脏工作区一律拒绝出正式包。"""
    commit = git_output("rev-parse", "HEAD")
    if not commit:
        raise RuntimeError("当前目录不是可用的 Git 仓库，无法保证版本可追溯。")
    if worktree_dirty():
        raise RuntimeError(
            "工作区存在未提交改动，禁止从 dirty worktree 构建正式版。\n"
            "请先提交并打 tag（commit → tag → build → release），或用 --allow-dirty 显式承担风险。"
        )
    tags = head_tags()
    wanted = expected_tag or f"v{project_version()}"
    if wanted not in tags:
        raise RuntimeError(
            f"HEAD（{commit[:12]}）上没有 tag {wanted}；已存在的 tag：{tags or '无'}。\n"
            "请先打 tag 再构建，确保 version.json / Git commit / __version__ 三者可追溯。"
        )
    return {"commit": commit, "tags": tags, "version": project_version()}


def ensure_layout() -> None:
    for name in ("current", "previous", "staging", "upgrade_logs"):
        (RELEASE / name).mkdir(parents=True, exist_ok=True)
    (ROOT / "backups" / "database").mkdir(parents=True, exist_ok=True)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def schema_version(database: Path) -> str:
    if not database.is_file():
        return "暂无数据"
    with sqlite3.connect(database) as connection:
        row = connection.execute("SELECT version FROM schema_meta LIMIT 1").fetchone()
        return str(row[0]) if row else "暂无数据"


def backup_database(database: Path, keep: int = 5) -> Path | None:
    if not database.is_file():
        return None
    target_dir = ROOT / "backups" / "database"
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / f"mechanical_project_namer_{datetime.now():%Y%m%d_%H%M%S_%f}.db"
    with closing(sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True)) as connection:
        check = connection.execute("PRAGMA integrity_check").fetchone()
        if not check or str(check[0]).lower() != "ok":
            raise RuntimeError(f"SQLite integrity check failed: {check}")
        with closing(sqlite3.connect(target)) as destination:
            connection.backup(destination)
    with closing(sqlite3.connect(target)) as connection:
        check = connection.execute("PRAGMA integrity_check").fetchone()
        if not check or str(check[0]).lower() != "ok":
            raise RuntimeError("Copied SQLite backup failed integrity check")
    return target


def validate_package(directory: Path) -> dict:
    """Reject stale metadata or an EXE built from another source revision."""
    from PyInstaller.archive.readers import CArchiveReader

    metadata = json.loads((directory / "version.json").read_text(encoding="utf-8"))
    exe = directory / "机械项目命名助手.exe"
    if sha256(exe) != metadata.get("exe_sha256", "").upper():
        raise RuntimeError("EXE 校验值与 version.json 不一致，禁止发布。")
    archive = CArchiveReader(str(exe))
    identity_name = next((name for name in archive.toc if name.replace("\\", "/") == "assets/build_info.json"), None)
    if identity_name is None:
        raise RuntimeError("EXE 缺少内嵌构建信息，禁止发布。")
    embedded = json.loads(archive.extract(identity_name))
    for key in ("version", "git_commit", "git_dirty"):
        if embedded.get(key) != metadata.get(key):
            raise RuntimeError(f"EXE 内嵌构建信息与 version.json 不一致：{key}")
    if metadata.get("git_dirty") is not False or f"v{metadata['version']}" not in metadata.get("git_tags", []):
        raise RuntimeError("发布包不是已提交、已打 tag 的正式构建。")
    tagged = git_output("rev-parse", f"v{metadata['version']}^{{commit}}")
    if tagged != metadata.get("git_commit"):
        raise RuntimeError("版本 tag 与构建提交不一致。")
    return metadata


def write_version(staging: Path, previous_version: str, tests_passed: int,
                  *, allow_dirty: bool = False) -> Path:
    from mechanical_project_namer.data.database import SCHEMA_VERSION
    exe = staging / "机械项目命名助手.exe"
    if not exe.is_file():
        raise FileNotFoundError(exe)
    version = project_version()
    if allow_dirty:
        commit = git_output("rev-parse", "HEAD")
        tags = head_tags()
        dirty = worktree_dirty()
    else:
        info = guard_clean_worktree(expected_tag=f"v{version}")
        commit, tags, dirty = info["commit"], info["tags"], False
    data = {
        "version": version, "git_commit": commit, "git_tags": tags, "git_dirty": dirty,
        "build_time": datetime.now().astimezone().isoformat(timespec="seconds"),
        "exe_sha256": sha256(exe),
        "database_schema": str(SCHEMA_VERSION),
        "tests_passed": int(tests_passed), "previous_version": previous_version,
    }
    target = staging / "version.json"
    target.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return target


def log_event(event: str, **details) -> None:
    ensure_layout()
    target = RELEASE / "upgrade_logs" / f"{datetime.now():%Y%m%d_%H%M%S}_{event}.json"
    target.write_text(json.dumps({"event": event, "time": datetime.now().astimezone().isoformat(), **details}, ensure_ascii=False, indent=2), encoding="utf-8")


def upgrade(staging: Path, database: Path | None = None) -> Path:
    ensure_layout()
    staging = staging.resolve()
    if staging.parent != RELEASE.resolve() or staging.name != "staging":
        raise RuntimeError("只允许发布 release/staging，防止误移动其他目录。")
    current = RELEASE / "current"
    previous = RELEASE / "previous"
    exe = staging / "机械项目命名助手.exe"
    if not exe.is_file():
        raise FileNotFoundError(f"staging package missing: {exe}")
    validate_package(staging)
    if database:
        backup = backup_database(database)
    else:
        backup = None
    archived_previous = None
    if previous.exists():
        archived_previous = RELEASE / f"archived_previous_{time.time_ns()}"
        previous.rename(archived_previous)
    moved_current = False
    moved_staging = False
    try:
        if current.exists():
            current.rename(previous)
            moved_current = True
        staging.rename(current)
        moved_staging = True
        log_event("upgrade_success", exe_sha256=sha256(current / "机械项目命名助手.exe"), database_backup=str(backup or ""))
        return current
    except BaseException:
        if moved_staging and current.exists():
            current.rename(staging)
        if moved_current and previous.exists() and not current.exists():
            previous.rename(current)
        if archived_previous and archived_previous.exists() and not previous.exists():
            archived_previous.rename(previous)
        log_event("upgrade_rollback", reason="切换失败")
        raise


def rollback() -> Path:
    ensure_layout()
    current, previous = RELEASE / "current", RELEASE / "previous"
    if not previous.is_dir() or not (previous / "机械项目命名助手.exe").is_file():
        raise FileNotFoundError("previous package is unavailable")
    failed = RELEASE / f"failed_{int(time.time())}"
    if current.exists():
        current.rename(failed)
    previous.rename(current)
    failed.rename(previous)
    log_event("manual_rollback", restored=str(current))
    return current


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    version = sub.add_parser("version")
    version.add_argument("--staging", type=Path, required=True)
    version.add_argument("--previous-version", default="1.6.0")
    version.add_argument("--tests-passed", type=int, default=0)
    version.add_argument("--allow-dirty", action="store_true",
                         help="危险：允许在脏工作区/无 tag 的情况下生成 version.json")
    preflight = sub.add_parser("preflight", help="校验 commit/tag/版本号是否一致")
    preflight.add_argument("--tag", default="")
    backup = sub.add_parser("backup-db")
    backup.add_argument("database", type=Path)
    up = sub.add_parser("upgrade")
    up.add_argument("--staging", type=Path, default=RELEASE / "staging")
    up.add_argument("--database", type=Path)
    sub.add_parser("rollback")
    args = parser.parse_args()
    if args.command == "version":
        ensure_layout()
        print(write_version(args.staging.resolve(), args.previous_version, args.tests_passed,
                            allow_dirty=args.allow_dirty))
        return 0
    if args.command == "preflight":
        info = guard_clean_worktree(expected_tag=args.tag)
        print(json.dumps({**info, "expected_tag": args.tag or f"v{info['version']}"}, ensure_ascii=False, indent=2))
        return 0
    if args.command == "backup-db":
        print(backup_database(args.database.resolve())); return 0
    if args.command == "upgrade":
        print(upgrade(args.staging, args.database)); return 0
    print(rollback()); return 0


if __name__ == "__main__":
    raise SystemExit(main())
