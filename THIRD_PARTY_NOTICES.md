# 第三方依赖说明

Phase4A账号服务固定Argon2id库及传递依赖，官方来源、wheel哈希、Windows/Linux实际兼容和完整许可见[账号组件记录](third_party/accounts/README.md)。服务端不依赖Qt、COM或CAD，未分发服务器二进制或安装包。

## Phase 2A 新增记录

离线GLB查看器新增本地捆绑JS（MIT许可的Online 3D Viewer/three.js/fflate），以及可选PyQt6-WebEngine；完整许可、版本、来源和安全修复见 [third_party/preview](third_party/preview/README.md)。本阶段运行栈为PyQt6及绑定6.11.0、Qt/WebEngine运行库6.11.2，未分发Qt二进制或安装包。下表保留原安装包历史构建环境，不代表Phase 2A使用旧Qt。

## 原安装包许可快照

源码仓库未捆绑第三方库二进制、原图标或 SOLIDWORKS 软件。Windows 安装包包含必要运行库，完整原始许可与归属放在安装目录的 `licenses`，对应源码材料放在同一 Release。以下依据构建环境元数据记录，依赖自身的版权与完整条款仍然有效。

| 依赖 | 版本 | 元数据许可证 |
| --- | --- | --- |
| PyQt6 | 6.8.1 | GPL v3 |
| Qt (PyQt6-Qt6) | 6.8.2 | LGPL v3 |
| PyQt6-sip | 13.12.0 | BSD-2-Clause |
| httpx | 0.28.1 | BSD-3-Clause |
| keyring | 25.6.0 | MIT |
| pywin32 | 312 | PSF，分组件另有许可 |
| openpyxl | 3.1.5 | MIT |
| pytest / pytest-qt | 8.3.5 / 4.4.0 | MIT |
| PyInstaller（可选构建） | 6.12.0 | GPLv2-or-later，附分发例外 |

PyQt 官方说明：https://riverbankcomputing.com/software/pyqt/intro

商业与 GPL 许可说明：https://riverbankcomputing.com/commercial/license-faq

安装包还包含 CPython 及其运行库、httpcore、anyio、h11、idna、certifi、et_xmlfile、jaraco 系列和相应传递依赖。实际构建环境清单、版本与各组件原始许可随包提供，完整源码材料的文件与哈希见 Release。许可收集也可能包含只用于构建/测试的环境组件，它们不意味着其全部代码都随程序执行。

Python、libffi、OpenSSL、SQLite 及 Microsoft 运行库保留各自条款。Qt DLL 动态链接且未修改，允许以相容的修改版本替换和调试。Microsoft 运行库仅用于受支持的 Windows 平台，不被重新授予 GPL 许可。程序不附加第三方许可之外的使用或调试限制。

本清单不替代随包第三方完整许可文本。本项目自有代码采用 GPL-3.0-only，第三方组件不会因此被重新许可。

Phase 2B离线PDF使用现有PyQt6/Qt6的QtPdf（PDFium）模块，未捆绑新的二进制或生成安装包。实际锁定版本、QtPdfWidgets兼容验证、许可及测试工具归属见 [预览组件记录](third_party/preview/README.md)；本机PDF不进入仓库、不被重新许可。

Phase 2C增加occt-import-js0.0.23（LGPL2.1）本地WASM能力，代码、许可和安全限制见同一组件记录。WASM与可选OCP测试写入器均不提交二进制，不改变既有组件许可。本轮没有安装包、真实CAD或源工程分发。
# Phase4C HTTPS隔离入口

新增组件的固定版本、官方来源/签名/哈希及原许可证见[third_party/https](third_party/https/README.md)。Nginx/libssl只解包到独立用户prefix，Uvicorn/h11只在专用venv；测试CA/私钥/制品/运行资料不在仓库。不是系统服务或正式安装包。
