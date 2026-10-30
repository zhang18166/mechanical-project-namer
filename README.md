# 机械项目命名助手 V1.7.1

本分支完成 **Phase3C.1：本机隔离发布协议范围PASS**；809测试、MB级真实GUI断流/恢复、180轮30分钟资源验收及范围见[PHASE3C1](docs/workbench/PHASE3C1.md)。启动入口`scripts/dev_publish.ps1`，默认使用工作树外独立数据目录；不连接腾讯云/飞牛，不代表生产放行。下方Phase3C的PARTIAL保留为历史说明。

本分支新增 **Phase3C本机回环发布实验，PARTIAL**，见[PHASE3C](docs/workbench/PHASE3C.md)。独立测试服务/客户端启动停止脚本在该文档中；仅虚构数据，不连接真实腾讯云/飞牛，不作为公网服务。下方保留Phase3B使用说明及其限定验收，不能据此将3C或2D标为PASS。

当前新增 **Phase 3B：离线归档库配套备份与整体恢复**，范围、格式、维护模式及验收见 [PHASE3B](docs/workbench/PHASE3B.md)。继承 [Phase 3A](docs/workbench/PHASE3A.md)；**Phase 2D仍为PARTIAL**，内存趋势、并发诊断超时和STEP补丁问题保持未关闭，见 [PHASE2D](docs/workbench/PHASE2D.md)。在本阶段独立工作树执行：

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[test]"
.\scripts\dev_backups.ps1
.\scripts\dev_backups.ps1 -Smoke
.\scripts\restore_library.ps1
.\scripts\test_workbench.ps1
```

使用“备份与恢复”打开独立维护窗口；创建前从托盘退出所有源客户端，不能仅隐藏窗口。默认配置在 `%LOCALAPPDATA%\MechanicalWorkbenchDev\phase3b`，支持 `-DevRoot <仓库外新目录>`。数据库损坏时直接运行 `scripts/restore_library.ps1`，无需启动主客户端或初始化旧库。备份是**本地未加密私有目录集**，包含配套SQLite及全部完整归档版本，排除未归档工作文件、凭据、缓存/日志。恢复仅创建新位置；不会覆盖原库或自动切换正式实例。具体路径重绑定与旧工作文件“待重新关联”见阶段文档。

“项目文件 → 创建本地版本 / 版本历史”和原预览继续保留。归档、备份和整体恢复均仅验收隔离开发范围，不要求WebEngine、QtPdf、Node或CAD解析器；预览仍按需加载。STEP不开放普通模式。不要用普通启动示例升级正式数据库。

本分支继承公开 V1.7.1 源码及Phase 0–3A，应用仍为1.7.1、Schema13；Phase3B没有新增数据库迁移。阶段事实见 [PROGRESS](docs/workbench/PROGRESS.md)，需求、基线与计划见 [docs/workbench](docs/workbench/BASELINE.md)。历史 MSI 预览仍为1.6.1；本次不构建安装包、不迁移生产数据。旧打包脚本含1.6.1固定元数据，未验收其用于当前版本。

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

## 隔离开发与测试

```powershell
.\scripts\dev.ps1
.\scripts\dev.ps1 -Smoke
.\scripts\test_workbench.ps1
```

开发数据默认在 `%LOCALAPPDATA%\MechanicalWorkbenchDev\phase0`；可用 `-DevRoot <新的仓库外专用目录>` 指定另一份配置。开发模式隔离数据、设置、导出、日志、缓存及单实例标识，禁用真实 AI、Windows 凭据、SOLIDWORKS 与全局快捷键；只使用虚构数据。`-Smoke` 打开真实窗口并生成仓库外报告后退出。此模式不是操作系统访问沙箱，不将项目指向真实 CAD 或网络共享。

测试默认使用独立配置并阻止真实网络、凭据、COM 和热键；完整 JUnit 保存在仓库外。本分支沿用公开仓库的 Qt 标准图标，不上传本机图标资源。

## 项目文件（Phase 1）

从首页上方“项目文件”进入。左侧选择已有项目，右侧添加本地文件或目录并后台扫描；支持搜索、类型筛选、取消、重新扫描、关联已有零件、打开和定位。复用原项目身份，不占用编号、不改名/移动/删除源文件。

识别 SLDPRT/SLDASM/SLDDRW、STEP/STP、STL、GLB、PDF，仅登记扩展名、路径和元数据，不解析或内嵌预览。每批限制为128个来源、10,000项、15秒、32层，拒绝链接、UNC/映射网络盘和磁盘根。只使用隔离数据库和虚构文件；取消需等待当前系统I/O返回。

## 使用

Phase4A隔离账号/远端项目入口为`scripts/dev_accounts.ps1`，在项目文件页按需登录本机测试服务；本地功能不依赖登录。普通用户Linux服务、管理员CLI、依赖安装、权限和验证范围见[PHASE4A](docs/workbench/PHASE4A.md)。只监听回环，未部署腾讯云或飞牛，不是公网服务使用说明。

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
