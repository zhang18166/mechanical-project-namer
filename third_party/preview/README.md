# 离线查看组件来源与许可

## Phase 2D：内核来源与补丁复核（2026-10-29）

结论PARTIAL，完整CVE范围、准确源码、资源逐字节核对及构建评估见[STEP_SECURITY.md](STEP_SECURITY.md)。当前组件没有安全补丁确认，STEP继续限定隔离开发模式；未升级运行内核。补充固定OCCT提交的LGPL许可例外原文，不分发新二进制。

## Phase 2C：受控STEP / STL（2026-10-28）

复用Online 3D Viewer0.18.0与现有Qt6.11栈，新增加固定的 [occt-import-js0.0.23](https://www.npmjs.com/package/occt-import-js/v/0.0.23)。官方npm元数据、tarball完整性和package-lock已核对；上游main显示0.0.24，但registry实际最新发布为0.0.23，不混称版本。客户端运行无需Node，安装验证使用既有Node24.16.0/npm11.13.0，没有全局升级。

源库：[官方源码](https://github.com/kovacsv/occt-import-js/tree/c2148e54b456b571238d35cac037d304053d64b2)，包含构建入口；对应OCCT子模块固定为 `d2abb6d844231cb8f29be6894440874a4700e4a5`。两个运行文件JS/WASM从官方npm安装到忽略目录并按SHA-256验证后才提供精确路由；Git不包含WASM、node_modules、测试模型或DLL。原LGPL2.1文本分别保留为occt-import-js-LICENSE.txt和OCCT-LICENSE.txt；组件不被重新许可，与本项目GPLv3的使用保持原有修改/再分发义务。将来分发二进制时还须核对对应源码、构建和完整第三方通知，本轮不发布安装包。

实际安全依据：[CVE-2026-42481](https://nvd.nist.gov/vuln/detail/CVE-2026-42481)、[上游解析加固PR1514](https://github.com/Open-Cascade-SAS/OCCT/pull/1514)。该PR于2026-09-01合并；候选原生OCCT8.0.1 tag早于该合并，GitHub compare显示未包含它，不能因wheel较新就认为已修复。客户端没有采用原生OCP读文件；固定WASM也不作“所有内核漏洞已修复”的声明。npm审计0项不覆盖原生OCCT安全问题；防护是WASM线性内存/虚拟文件系统、WebWorker终止、Windows进程内存预算、超时、词法/几何复杂度和无外网/无主机路径能力边界，细节与风险见PHASE2C。

可选fixture写入器：官方PyPI `cadquery-ocp-novtk8.0.1.1.0`及同版proxy，SHA-256锁定于requirements-cad-fixtures.lock。绑定源 [CadQuery/OCP](https://github.com/CadQuery/OCP)、[官方构建流程](https://github.com/CadQuery/ocp-build-system) 为Apache-2.0，所含OCCT仍按其原LGPL条款。该可选工具只在独立测试生成进程创建我们的形体/STEP，不读取任何输入CAD；不作为客户端依赖、后备解析路线或安装包内容。没有自行编写CAD内核或执行SOLIDWORKS转换。

资源、许可的确切哈希与来源保留在sources.json。STL是受限三角数据读取和法向重算，随后由原查看器渲染；STEP几何、曲面与网格化由OCCT/WASM完成。真实图纸及截图均不进入Git。

## Phase 2B：QtPdf（2026-10-28）

PDF后端使用已锁定的PyQt6 6.11.0 / PyQt6-Qt6 6.11.2，运行时未新增或升级第三方库。本地wheel RECORD核实QtPdf.pyd、QtPdfWidgets.pyd属于PyQt6，Qt6Pdf/Qt6PdfWidgets DLL属于PyQt6-Qt6；PDF不要求WebEngine、Node或浏览器资源。

官方依据：[Qt 6.11 PDF模块](https://doc.qt.io/qt-6.11/qtpdf-index.html)、[QPdfView](https://doc.qt.io/qt-6.11/qpdfview.html)、[模块及PDFium许可](https://doc.qt.io/qt-6.11/qtpdf-licensing.html)、[已验收版本QPdfView源码](https://github.com/qt/qtwebengine/blob/v6.11.2/src/pdfwidgets/qpdfview.cpp)、[QPdfDocument源码](https://github.com/qt/qtwebengine/blob/v6.11.2/src/pdf/qpdfdocument.cpp)、[官方PyQt6 6.11.0 wheel](https://pypi.org/project/PyQt6/6.11.0/)。官方在线文档可能随补丁滚动，实际二进制固定6.11.2。QPdfView可用并已真实显示4页样本，最终使用QtPdf原生栅格化配合受限子进程，以满足取消和资源预算；没有复制/改写Qt源码。

PyQt6绑定GPLv3与本项目GPL-3.0-only兼容。QtPdf由Qt提供商业/LGPLv3/GPL选项，源码SPDX同时列明GPLv3；PDFium及其第三方组件保持原许可义务。新增Qt-runtime-LGPL3.txt来自实际官方PyQt6-Qt6 6.11.2 wheel，PyQt GPL文本已保留；不将该文本视为替代PDFium/Chromium所有第三方通知。此阶段仅源码与声明归档，不捆绑DLL或发布安装包；将来分发二进制时须重新收集对应完整第三方许可。

测试生成器依赖reportlab4.4.9（BSD）、pillow12.3.0（MIT-CMU）、charset-normalizer3.5.2（MIT），仅用于自行生成虚构样本，不由客户端导入或分发；来自官方PyPI，精确清单见requirements-pdf-test.lock。真实本机PDF及截图不纳入Git，其内容不被重新许可。

以下保留Phase 2A组件记录。

此目录保留新增组件许可；现有项目GPL-3.0-only及旧安装包声明保持有效。本轮不分发Qt或Chromium二进制，不构建安装包；通过官方PyPI安装的运行库自带其完整许可和第三方通知。

| 组件 | 锁定版本 | 用途 / 许可 |
| --- | --- | --- |
| PyQt6 / PyQt6-WebEngine | 6.11.0 / 6.11.0 | Python绑定，GPL-3.0-only；本目录保留官方wheel许可 |
| PyQt6-Qt6 / PyQt6-WebEngine-Qt6 | 6.11.2 / 6.11.2 | Qt动态运行库、Chromium；遵循Qt及各第三方原许可，安装来源官方PyPI，不在Git中捆绑 |
| PyQt6-sip | 13.13.0 | 绑定支持，BSD-2-Clause；完整环境见requirements-preview.lock |
| Online 3D Viewer | 0.18.0 | 成熟查看器和GLB导入，MIT |
| three.js | 0.176.0 | OV使用的渲染库，MIT；未自行编写渲染内核 |
| fflate | 0.8.3 | OV的依赖，MIT；强制覆盖上游0.8.2后重建 |
| esbuild | 0.28.2 | 仅构建IIFE脚本，MIT；构建工具和node_modules不提交 |

运行资源为 `mechanical_project_namer/preview_assets` 的本地HTML、脚本和已构建JS。未引用CDN、远程字体、在线环境贴图。本阶段不接受Draco、meshopt、KTX/Basis等扩展，无需下载这些解码器。标准内嵌PNG由浏览器本地解码，并在进入浏览器前限制数量和尺寸；JPEG纹理及压缩扩展给出不支持提示。

官方来源与实际核实依据：

- [Online 3D Viewer代码与嵌入文档入口](https://github.com/kovacsv/Online3DViewer)、[开发文档](https://kovacsv.github.io/Online3DViewer)。实际嵌入使用官方npm包内的EmbeddedViewer、LoadModelFromUrlList、GetViewer/FitSphereToWindow/SetCamera API，固定版本源码已检查。
- [PyQt6-WebEngine官方PyPI元数据及GPL说明](https://pypi.org/project/PyQt6-WebEngine/6.11.0/)、[Qt 6.11 profile API](https://doc.qt.io/qt-6.11/qwebengineprofile.html)、[Qt WebEngine安全说明](https://doc.qt.io/qt-6.11/qtwebengine-security.html)。
- [fflate ZIP64无限循环漏洞 GHSA-px8p-9vwx-vf98](https://github.com/advisories/GHSA-px8p-9vwx-vf98)：审查时发现上游0.8.2存在漏洞，改用修复版0.8.3；不是保留已知漏洞后仅靠禁用功能忽略。重建后npm审计0项已知问题；这不等于证明组件没有未知漏洞。

`preview-web/package-lock.json`记录精确依赖、官方registry下载地址和完整性；`sources.json`记录原始源包、许可与最终JS的SHA-256。构建命令为 `scripts/build_preview.ps1`，执行npm ci（忽略安装脚本）、审计与固定esbuild构建。已重复构建并核对JS哈希一致；安装/构建允许联网，运行预览不联网。

旧归档Qt6.8仅用于最初421项基线复测；新工作树中的Qt栈单独升级到以上匹配版本以采用较新WebEngine。其他顶层业务依赖未盲目升级，完整传递版本锁定于requirements-preview.lock。原虚拟环境未修改。Qt绑定的编译版本常量为6.11.0，实际动态Qt及WebEngine为6.11.2，实际Chromium版本140.0.7339.225；不把版本号表述为生产安全认证。
