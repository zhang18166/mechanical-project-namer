# 机械项目命名助手 V1.7.1

本分支先归档经审查的本地 V1.7.1 源码基线；历史 MSI 预览仍为 1.6.1。本次不构建安装包、不迁移生产数据、不进入 Phase 2；后续 Phase 0 提交提供隔离开发入口。旧打包脚本含 1.6.1 固定元数据，未验收其用于当前版本。

Windows 桌面工具，管理机械项目代号、模块和零件编号，支持名称复制、历史记录、SQLite 本地存储、CSV / Excel / PDF 导出、可选 AI 命名和 SOLIDWORKS 联动。

2026-10-12：从 1.6.1 源码整理首个开源版本，改用 Qt 标准图标，使用虚构测试数据，以 GPL-3.0-only 发布。

2026-10-12：新增 Windows x64 MSI 预发布构建、离线程序自检和后续修改同步流程。

## Windows 安装包

在 [Releases](https://github.com/zhang18166/mechanical-project-namer/releases) 下载标记为预发布的 MSI，先核对随附 SHA256。包未签名，Windows 可能提示未知发布者；不要绕过系统安全策略。安装/卸载方式、数据位置和未测范围见 [安装说明](packaging/INSTALL.md)。交互 GUI、真实 AI/SOLIDWORKS 和系统安装/卸载尚未验收。

## 安装与运行

需要 Windows 和 Python 3.12。SOLIDWORKS 联动需要本机安装对应软件；基础命名功能不需要 AI 密钥。

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[test]"
.\.venv\Scripts\python.exe -m mechanical_project_namer
```

也可运行 `launcher.py`。仓库仅保存源码，安装包在 Releases；构建方法见 [packaging/README.md](packaging/README.md)。

## 使用

简化首页使用设备和自定义模块，例如虚构项目 `DEMO`、模块 `DJ`；名称示例 `DEMO-DJ-001-演示连接块`。预览后可仅登记名称，或通过另行验证的 SOLIDWORKS 联动创建文件。

预览和复制不递增编号；成功保存才推进编号。简化首页采用每项目/模块共享 001–999 通用编号，历史分类接口保留总成 `000` 单例；已使用和已作废编号不自动回收。

在设置页配置可选 AI 服务和模型。API Key 使用 Windows 凭据管理器保存。使用 AI 时，项目描述、命名上下文和历史示例可能发送到所选服务，勿输入不允许外发的资料。

数据位于 `%LOCALAPPDATA%\MechanicalProjectNamer`，设置位于 `%APPDATA%\MechanicalProjectNamer`。发布源码不包含运行数据，卸载或迁移前自行备份。

```powershell
.\.venv\Scripts\python.exe -m pytest tests -q
```

核心测试覆盖项目编号独立性、保存事务、重复编号、作废编号、项目代号修改确认和中文搜索。GUI、真实 AI 服务和真实 SOLIDWORKS 联动需要另行验收。后续修复与改进经审查测试后同步本仓库，流程见 [CONTRIBUTING.md](CONTRIBUTING.md)。

## 许可证

Copyright (C) 2026 zhang18166。

本项目以 GNU General Public License version 3 only（SPDX: `GPL-3.0-only`）发布，完整条款见 [LICENSE](LICENSE)。你可以依据该许可证使用、修改和分发本项目。本项目不提供任何担保，包括适销性或特定用途适用性的担保。

第三方组件保留各自版权和许可证，详见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。SOLIDWORKS 为独立商业软件，不随本项目分发。
