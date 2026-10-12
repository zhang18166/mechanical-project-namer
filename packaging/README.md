# Windows 安装包构建与验证

2026-10-12：新增 Windows x64 MSI 预发布流程，使用 Python 3.12 的 msilib 与 PyInstaller 6.12.0，不需要额外安装 MSI 编译器。

## 构建

在干净的公开源码提交上构建。Python 需为 Windows x64 3.12。安装项目声明的 build/test 依赖，保留实际环境的完整版本清单。

```powershell
python -m pytest tests -q
python -m PyInstaller --noconfirm --distpath build/dist --workpath build/pyinstaller packaging/app.spec
python packaging/collect_licenses.py SOURCE_INPUTS build/dist/MechanicalProjectNamer
```

`SOURCE_INPUTS` 应包含已核验哈希的 PyQt6 6.8.1、openpyxl 3.1.5、PyInstaller 6.12.0 源码包，以及 Qt 6.8.2 的 qtbase、qtsvg、qtimageformats、qttranslations 源码包。下载来源和 SHA256 随 Release 的源代码材料提供。不要执行下载的构建脚本来收集许可。

`packaging/runtime-licenses` 保留 Python 所附运行库的 OpenSSL-LICENSE.txt 和 libffi-LICENSE.txt。OpenSSL 文本来自官方 `openssl-3.5.7` 标签，其他运行库文本来自 `astral-sh/python-build-standalone` 的 `20260814` 标签，与本次 Python 构建一致，原版权完整保留。脚本保留原始许可与 Qt 归属材料；若任何打包组件缺许可，停止发布。Qt PDF 图像读取插件不属于此程序功能，spec 明确排除 qpdf.dll/Qt6Pdf.dll；PDF 输出使用 QtPrintSupport。

可运行 `python packaging/fetch_sources.py SOURCE_INPUTS` 按 `third-party-sources.json` 下载和校验上游源码；此步骤不执行下载内容。环境版本见 `requirements-build.lock`，运行库更新后必须重新核查许可。

完整流水线：在干净提交上创建待发布 tag，然后运行 `python packaging/build_windows.py SOURCE_INPUTS NEW_OUTPUT_DIR --tag v1.6.1-windows-preview.1`。该脚本重新运行测试、冻结程序、收集许可、归档精确源码、生成 MSI、提取并比对 CAB 文件、生成 SHA256；只把 `NEW_OUTPUT_DIR/assets` 中的审查后资产上传 Release，日志和临时测试目录不发布。

将仓库 `LICENSE`、`THIRD_PARTY_NOTICES.md`、`packaging/INSTALL.md` 复制到 payload 根目录。用 `git archive` 导出当前精确提交的公开源码到 `source.zip`，放入 payload；写入不包含本机路径的 `build-info.json`（commit、tag、版本、架构、工具版本）。使用一个全新的输出目录，不覆盖旧构建。

```powershell
build/dist/MechanicalProjectNamer/MechanicalProjectNamer.exe --self-test build/self-test.json
python packaging/build_msi.py build/dist/MechanicalProjectNamer build/MechanicalProjectNamer-1.6.1-preview.1-windows-x64.msi
Get-FileHash build/MechanicalProjectNamer-1.6.1-preview.1-windows-x64.msi -Algorithm SHA256
```

构建步骤可重复执行，但 MSI GUID、时间戳和打包环境可能造成字节差异，不宣称位级可复现。升级版本需要为新 MSI 分配新的 ProductCode，并设计/验证升级逻辑；当前预发布没有自动升级，先卸载旧预览包再安装。

## 发布门禁

- 核对最终 Git tree、唯一对应 commit/tag、源码归档、各资产 SHA256；重新扫描所有拟发布内容。构建日志不上传，避免泄露本机路径。
- 检查 MSI 的 File、Component、Directory、Property、InstallExecuteSequence 表以及嵌入 CAB；不得出现未经审查的自定义动作、服务、文件关联、启动项或计划任务。
- 在新建的隔离目录执行 `--self-test`。它只使用临时虚构数据，不调用网络、Windows 凭据或 SOLIDWORKS。离屏 Qt 检查不等于交互界面验收。
- 安装/卸载真实测试会登记 Windows Installer 产品、写安装目录和卸载信息。未获得该测试的明确授权时，只做包内容检查，标记未完成实际安装/卸载验收。
- 同一 Release 提供 MSI、SHA256SUMS、精确项目源码、依赖对应源码和许可材料。许可证文字/归属必须随安装内容提供，不能只有网页链接。
- 未验证 GUI、系统安装/卸载或真实联动时标为 prerelease，并在说明中逐项列明。

后续 bug 修复及功能完善按根目录 `CONTRIBUTING.md` 增量同步，仅限本项目经过审查的改动。
