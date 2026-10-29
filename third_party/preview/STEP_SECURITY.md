# STEP 内核安全核验（Phase 2D）

核验日期：2026-10-29。**PARTIAL：当前发布组件没有得到补丁覆盖确认，STEP继续仅在隔离开发模式开放。** 进程预算、WASM、词法过滤与无网络能力是缓解边界，不能替代内核修复；不以npm审计零项作为OCCT安全证明。

## 当前运行资源与源码对应

- 官方npm发布包仍为`occt-import-js 0.0.23`，gitHead为`c2148e54b456b571238d35cac037d304053d64b2`；[官方发布记录](https://github.com/kovacsv/occt-import-js/releases/tag/0.0.23)明确回退到OCCT7.6.1并使用Emscripten3.1.69。main的0.0.24不是已发布的修复版。
- [该提交的OCCT子模块](https://github.com/kovacsv/occt-import-js/tree/c2148e54b456b571238d35cac037d304053d64b2/occt)固定为`d2abb6d844231cb8f29be6894440874a4700e4a5`；其`Standard_Version.hxx`为7.6.1，提交说明亦为更新7.6.1。
- npm WASM与该提交dist中的WASM逐字节相同：7,604,031字节，SHA-256 `33391fc9d94ea5c869a6718488bf0a9a464222bac9bdc764dfe1690cef281952`。
- npm JS为96,871字节，SHA-256 `3fb44ce11d00611f9b3f3c5775d520ebab48930c1f08279b7b1316f05f0d3379`；提交中的JS为96,852字节，SHA-256 `8b090be386bc8ffba32222aa19f38742ae8ed35707819beb7f8ab4938a28c97a`。差异仅为换行符，归一化CRLF/LF后内容完全相同。两者的原始哈希不可混用。
- 以上确认发布资源和声明源码的可追溯关系；尚未通过同一源码的独立、重复重建证明原始WASM的构建可重复性，不能进一步声称发布二进制已经包含某个未记录补丁。

运行资源仍锁定于cad-web/package-lock.json和代码哈希校验，未更换或提交WASM。客户端没有原生CAD解析DLL，也不使用fixture写入器读取输入文件。

## CVE适用范围与代码证据

[CVE官方记录库](https://github.com/CVEProject/cvelistV5/tree/main/cves/2026/42xxx)中的六项描述以OCCT V8_0_0_rc5为对象，affected字段没有列出完整版本范围。不能据此编造“所有7.x均受影响”或“7.6.1不受影响”。本轮另检查实际固定源码及应用暴露路径：

另核对CVE所引用的[报告人原始通告](https://gist.github.com/sgInnora/dfba083d04906283e9c92aea78e2d94a/258a76d7ace12e897d4ef37ee065f56e98ae5c0c)：声明范围为OCCT不高于7.8.1及master截至c540f316，7.6.1落在其声明范围内。它与CNA描述版本的口径不同，不是完整的供应商版本矩阵；结合实际源码缺少保护，不能以CNA只写8.0.0rc5为由排除当前风险。本轮没有对每个旧版本或所有原生格式执行漏洞利用。通告原文SHA-256为`a7a9aa8620550f61da96a1974530835fd8f100a3f46b6a781caab51951392800`，副本仅保留仓库外。

| CVE | 原生解析问题 | 当前应用路径与结论 |
| --- | --- | --- |
| 2026-42476 | ASCII STL短行越界读取 | 应用STL由受限Python三角读取器处理，不进入OCCT STL读取器；不能把本行当作内核已修复证明 |
| 2026-42477 | OBJ短行索引读取 | 应用不接受OBJ，包装层没有OBJ读取入口；运行范围外 |
| 2026-42478 | VRML面索引/指针 | 应用和包装层不暴露VRML；运行范围外 |
| 2026-42479 | VRML线索引越界 | 同上 |
| 2026-42480 | VRML引号字符串越界 | 同上 |
| 2026-42481 | STEP B样条数据合同、方向边递归；另含IGES曲线问题 | **STEP相关且补丁未确认**；应用Worker只调用ReadStepFile，未开放包装层的IGES/BREP入口 |

[上游PR1514](https://github.com/Open-Cascade-SAS/OCCT/pull/1514)于2026-09-01合并，合并提交`573b4be6515268eff5a7c2acac183237d5388f70`。它增加行长度/终止符检查、OBJ/STL/VRML索引保护、STEP B样条数组/极点合同验证、方向边迭代解析与循环拒绝及对应GTest。

在当前7.6.1固定源码中，`StepShape_OrientedEdge::EdgeStart/EdgeEnd`直接递归调用edgeElement，没有该PR的迭代循环保护；`MakeBSplineCurveCommon`按重数数组数量读取knots，缺少该PR新增的配对长度及完整合同检查。因此不能确认当前内核修复，不能仅凭应用过滤器拒绝一个自引用样本而解除闸门。

源码证据SHA-256（原文、API响应和比对材料保存在仓库外）：

| 固定源码文件 | SHA-256 |
| --- | --- |
| src/StepShape/StepShape_OrientedEdge.cxx | `3c4dc1d026a7baa49374e143ae07e23cfaf4a0851070aac63ea6881a4a41c04b` |
| src/StepToGeom/StepToGeom.cxx | `dd5bd5a2dd4127c85eaed79a8fdb337521de17d3db814b53b034819862745d14` |
| src/Standard/Standard_ReadLineBuffer.hxx | `38614c6eed0c72d9be9e9b9031cb03bcb0b5bd33184fd4cc05c31d3501bf255d` |

## 替代与重建验收门槛

官方npm及main仍固定上述旧OCCT；在核对的官方发行路径中未找到可以直接采用、且确认覆盖PR1514的维护中WASM发行版。官方原生8.0.1发行早于该PR，不是已确认修复的替代品；不引入原生DLL。也检查了OpenCascade.js及其他STEP查看项目的源库入口，未取得其已覆盖本补丁且可直接替换的发行证明，不把项目存在或版本较新当作安全确认。

具体备选：[OpenCascade.js的构建定义](https://github.com/donalffons/opencascade.js/blob/master/Dockerfile)仍固定OCCT `bb368e271e24f63078129283148ce83db6b9670a`（2022-04-26）；package标为2.0.0-beta.b5ff984。核对的两个补丁为standard_time与using-statements，不含本轮目标STEP保护代码。未安装或把该备选接入客户端，也未据此作全库安全认证。

本轮在仓库外安装独立CMake3.31.6、Ninja1.11.1.4及官方便携Emscripten3.1.69，取固定包装层和PR1514合并源码，实际进行构建兼容探针。未改全局Python、注册表、永久PATH或现用软件。详细构建结果以PHASE2D.md为准，实验资源未接入客户端。

重新采用内核之前必须固定包装层、OCCT、SDK和构建工具的源码/版本及哈希，审查适配差异；完成可重复构建及补丁对应测试，并重新运行AP203/214/242、颜色/多实体、英寸/毫米、81件、离线/外网拒绝、异常/取消和完整长期GUI验收。不得沿用旧内核数据给新内核验收，也不得只改组件版本号。

## 许可证

原包装层LGPL-2.1与OCCT LGPL-2.1许可文本继续保留。本轮补充该OCCT固定提交的`OCCT_LGPL_EXCEPTION.txt`原文为`OCCT-LGPL-EXCEPTION.txt`，SHA-256 `04580a884ea6cea294402649ff7b5cbb167d47462d1340a4ed33e550db10a81b`，未重新许可上游代码。源码来源为[固定提交的许可例外](https://github.com/Open-Cascade-SAS/OCCT/blob/d2abb6d844231cb8f29be6894440874a4700e4a5/OCCT_LGPL_EXCEPTION.txt)。尚未分发二进制或安装包；未来分发仍须提供对应源码、修改和完整第三方通知。
