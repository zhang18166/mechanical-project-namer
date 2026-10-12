# Windows x64 预发布安装说明

需要 Windows 10/11 x64。此包未进行代码签名，Windows 可能显示未知发布者提示；请核对 GitHub Release 来源和 SHA256，遵循系统安全策略，不建议绕过安全警告。

双击 MSI，或使用 `msiexec /i MechanicalProjectNamer-1.6.1-preview.1-windows-x64.msi /qb` 安装。默认按当前用户安装至 `%LOCALAPPDATA%\Programs\MechanicalProjectNamer-Preview`，无需注册系统服务、文件关联、自启项或计划任务。不会创建桌面/开始菜单快捷方式，可从安装目录启动 `MechanicalProjectNamer.exe`。实际安装和卸载流程尚未在本次预发布中运行验收。

卸载：Windows“设置 → 应用 → 已安装的应用”，选择 `Mechanical Project Namer (Preview)`。也可运行 `msiexec /x {CD37DAB3-5BD3-5C77-B58A-093AF4F3E78D} /qb`。关闭程序后再卸载；卸载不会主动清理运行数据库、导出与设置。升级预览包前请先卸载旧预览包。

程序默认使用 `%LOCALAPPDATA%\MechanicalProjectNamer` 与 `%APPDATA%\MechanicalProjectNamer`。若同一账号已有旧版业务数据，运行新程序前应自行备份，勿将预发布直接用于唯一生产数据。本次构建与离线自检没有读取或修改这些生产目录。

包内 `source.zip` 是本安装程序对应的项目源码，`build-info.json` 记录提交；`LICENSE` 为 GPL-3.0-only 条款，`licenses` 保留第三方许可及归属。Qt/PyQt 和其他依赖的对应源码在同一 GitHub Release 的第三方源码资产中，源码下载不收费。Qt DLL 动态链接且未做修改，可以用相容的自行构建版本替换；允许为调试此类修改而逆向工程，不附加限制。

已验证：离线空数据库、基本命名编号、Qt 离屏控件、PDF/Excel 输出及核心自动化测试。未验证：交互 GUI、真实 AI、真实 SOLIDWORKS、系统安装/卸载。请按预发布软件使用。
