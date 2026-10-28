# 项目长期约束

- 在现有 Python/PyQt6 命名助手上增量开发；版本由 pyproject.toml 与包内 __version__ 保持一致，不按目录名判断。
- 开发入口：`scripts/dev.ps1`；测试入口：`scripts/test_workbench.ps1`。真实 AI、Windows 凭据、SOLIDWORKS 联动须单独明确授权及验收，模拟测试不能替代。
- 修改可能写数据的入口前，先定位真实路径并在仓库外做可恢复备份；SQLite 使用在线一致性备份，开发数据和进程资源与正式实例隔离。保留用户修改及现用软件。
- 不把真实 CAD、运行数据库、设置、密钥、个人绝对路径或备份纳入提交。本地报告放仓库外。已有 CAD 导入默认不改名、不移动，保护装配引用。
- 项目身份和编号体系复用现有数据；工作区与可清理缓存分开；不让多机直接共用 NAS 活动 SQLite。不擅自部署、迁移、提交或发布。
- 产品约束：[需求](docs/workbench/REQUIREMENTS.md)；实际验收与开发说明：[基线](docs/workbench/BASELINE.md)；阶段范围：[计划](docs/workbench/PLAN.md)。原本机的私有接管资料保持仓库外或原工作区；公开归档与排除范围见进度文档。

- 阶段完成后更新 [PROGRESS](docs/workbench/PROGRESS.md)；只从公开历史派生安全分支，禁止推送本机私有历史。详见 CONTRIBUTING.md。
