# Phase 4A：Linux服务端、管理员账号与远端项目索引

2026-10-30。**开发验收PASS（Windows客户端＋本机WSL2 Ubuntu26.04隔离服务范围）**，不代表Ubuntu22.04、HTTPS或生产部署认证。GitHub同步单独记入PROGRESS及最终远端核对。从公开`82a72679c1c55a30c597636fc59f9b3a66fecc07`派生独立分支`codex/mechanical-workbench-phase4a-auth`，工作树`<开发根>/_worktrees/mechanical-workbench-phase4a-auth`。原工作树和未提交修改保留。应用1.7.1、桌面Schema13不变；**Phase2D仍PARTIAL、STEP普通模式关闭**。

实现/验收归档提交`f24ac97bb40d24f143c26c8547cd272633986874`已安全推送同名独立分支并读回SHA一致，原有10个远端分支不变；本条同步记录为后续文档提交，不含运行数据。最终远端SHA以分支历史与交付报告核对为准。

## 范围与组件

新增独立`account_server`复用Phase3C.1的任务/上传/校验/封存/健康/下载实现，只给原服务增加两个事务内扩展钩子。原预置身份实验入口保留，账号服务不接受其固定令牌。服务器为纯Python+Argon2，不依赖Qt、COM、注册表、凭据管理器或CAD软件。无公开注册、匿名分享、云/NAS、转换、安装包或部署。

Argon2id及依赖固定版本、官方来源、完整许可见[third_party/accounts](../../third_party/accounts/README.md)。Windows基础客户端无需安装密码哈希库；账号窗口按需导入，未登录或断网不影响本地命名、文件、快照/备份及预览启动。

服务器只绑定127.0.0.1；HTTPServer单请求串行、原文件16MiB/版本64MiB等协议限制保留。会话默认1800秒、最多3600秒，每用户最多16个有效会话。账号/项目各256条；失败限流每用户名5次/60秒，未知用户名限流键最多4096条；Argon2计算仍有成本，**不是公网抗拒绝服务方案**。登录/管理JSON最多8KiB，清单沿用协议2MiB上限；不放开网络、证书或浏览器安全机制。

## 身份、会话与权限

用户使用稳定UUID，用户名不作为跨系统关联。bootstrap仅为本地离线CLI、需独占服务锁，首次创建管理员；无默认密码，不在HTTP提供初始化/注册接口，初始化标记或已有账号均拒绝再次bootstrap。新账号/管理员重置后必须先改初始密码，改密撤销现有会话。角色变更、停用、重置撤销全部会话；权限每次请求读取当前数据库，原会话不能沿用已撤销授权。最后一个启用管理员不能停用或降级。

数据库仅保存Argon2哈希及会话令牌的SHA-256，客户端令牌只在内存。每次认证API请求先确认回环服务UUID，端口不是身份；这不是TLS身份认证。退出按钮主动撤销服务端会话，关闭账号窗口只清本地令牌，服务端剩余会话到期失效。登录失败/未知账号/停用返回相同受控错误，不返回密码哈希、凭据、私有路径或他人清单；审计不保存请求头、密码和令牌。

| 能力 | 管理员 | 设计成员 | 只读成员 |
| --- | --- | --- | --- |
| 账号创建/角色/停用/重置/授权审计 | 可管理；保护最后管理员 | 拒绝 | 拒绝 |
| 项目元数据 | 全部可见 | 显式metadata_view | 显式metadata_view |
| GLB预览副本下载 | 显式preview_view | 显式preview_view | 显式preview_view |
| PDF副本下载 | 显式pdf_view | 显式pdf_view | 显式pdf_view |
| 原生/STEP/STL及其他源附件下载 | 显式source_download | 显式source_download | 显式source_download |
| 版本发布 | 显式version_publish | 显式version_publish | 始终拒绝 |
| 项目名称/描述/状态维护 | 可管理 | 显式project_manage | 始终拒绝 |
| 建立新远端项目及来源映射 | 管理员显式创建 | 拒绝 | 拒绝 |
| 分享访客、对外分享 | 模型预留，接口拒绝 | 拒绝 | 拒绝 |

非元数据权限必须同时显式包含metadata_view。下载同时检查文件角色与扩展名，不能把.prt谎报为预览以绕过源文件权限。客户端提交role/admin字段不能提升权限。项目UUID、来源store UUID和本地projects.id由管理员显式映射，同名项目、同值本地ID不自动合并。服务器不接受本地绝对路径作为来源。

## 索引与迁移、备份

独立服务索引`account_meta.schema=2`，不是桌面Schema2。保留原tasks及清单，新建users、sessions、login_limits、remote_projects、grants、auth_audit、task_provenance。项目保存UUID/名称/描述/状态/受控来源映射；版本复用协议清单及文件角色/相对路径/大小/哈希，增加发布人UUID、接受及完成时间。旧任务不伪造发布人或时间，明确legacy-unrecorded。文件字节健康、CAD依赖、预览对应分开，后两项仍unverified。

旧服务索引升级前用SQLite在线backup生成仓库外`migration-backups/before-accounts-<UUID>.sqlite`。增量表及映射在BEGIN IMMEDIATE内迁移，可重复执行；失败回滚。拒绝桌面schema_meta库及未知/未来账号schema，不自动迁移生产库。扩展钩子与原任务创建/完成登记共用事务，发布登记失败仍沿用已验证的文件系统提交恢复路径。

`backup-index`在服务停止、独占租约下做SQLite一致性备份，只写新目标。备份包含敏感账号哈希、授权和会话摘要，必须保密，不能提交Git。**索引备份不等于完整文件仓库备份，也不自动进入桌面3B备份。** 后续服务器配套备份应停服后同时保存配置中的服务UUID、索引、完整storage目录及所有者标记，逐项哈希和关联核验；恢复只写新隔离目录，先备份、重新核对UUID/清单并撤销全部旧会话，再另行演练。当前未提供整套服务器备份/恢复产品，不以索引quick_check冒充配套恢复成功。桌面3A/3B结构及白名单不变，账户密码/令牌不进入桌面备份。

## 接口与维护入口

| 路由 | 授权与功能 |
| --- | --- |
| GET /health | 无凭据的最小存活/UUID信息，不代表全部文件健康 |
| POST /auth/login | 用户名密码，统一拒绝响应、持久限流 |
| GET /auth/me；POST /auth/logout、/auth/password | 会话状态、撤销、改密；初始会话仅限这三项 |
| GET/POST /admin/users；PATCH /admin/users/{uuid} | 管理员列表/开户/角色及启停 |
| POST /admin/users/{uuid}/reset；POST /admin/grants | 管理员初始密码重置、替换项目授权 |
| POST /admin/projects；GET /admin/audit | 管理员明确映射、最近200条审计 |
| GET /projects；PATCH /projects/{uuid} | 已授权目录、项目管理；不允许修改来源映射 |
| GET /projects/{uuid}；原/tasks路由 | 原版本清单/文件级发布与下载，使用当前账号授权 |

以下均为人工隔离实验入口，替换占位路径，不自动注册后台服务。先审查本机环境，禁止全局升级。

```powershell
# Windows工作树，既有桌面环境之外只增加锁定账号依赖
.\.venv\Scripts\python.exe -m pip install --require-hashes --only-binary=:all: -r requirements-server.lock
.\scripts\dev_accounts.ps1
.\scripts\dev_accounts.ps1 -Smoke
.\scripts\test_workbench.ps1
```

客户端从“项目文件→测试服务登录 / 远端项目”进入；选择仓库外连接JSON，仅含`{"port":<测试端口>,"service":"<服务UUID>"}`。管理员可建立映射、开户、授权、停用、重置、查看审计；成员只见授权项目和版本。选择已有完整本地版本发布或下载到新目录，不直接打开服务器数据库。首次加载本地历史后需明确选版本再发布，避免隐式选择。

```sh
# Linux独立虚拟环境，无桌面依赖，普通用户运行
python3 -m venv <server-venv>
<server-venv>/bin/python -m pip install --require-hashes --only-binary=:all: -r requirements-server.lock
<server-venv>/bin/python -m mechanical_project_namer.account_server init --root <new-private-root> --config <private-config.json>
# getpass交互输入，不把密码放命令行。也支持所有者权限0600的仓库外password-file。
<server-venv>/bin/python -m mechanical_project_namer.account_server bootstrap --config <private-config.json> --username <test-admin>
<server-venv>/bin/python -m mechanical_project_namer.account_server serve --config <private-config.json> --ready <new-private-ready.json>
<server-venv>/bin/python -m mechanical_project_namer.account_server stop --ready <private-ready.json>
<server-venv>/bin/python -m mechanical_project_namer.account_server backup-index --config <private-config.json> --destination <new-private-backup.sqlite>
```

从项目根运行，或设置该进程的PYTHONPATH。数据、storage、配置各可指定仓库外路径。init拒绝已存在实例/配置；每次serve使用新的ready文件。Linux拒绝root、umask0077，服务限制512MiB地址空间、128文件描述符。stop核对真实服务PID、UID、模块及ready归属后SIGTERM。Windows启动/停止为`scripts/start_account_server.ps1`、`scripts/stop_account_server.ps1`；健康入口`scripts/check_account_server.py --connection <public-connection.json>`；管理员API CLI为`scripts/account_admin.py --help`。未启动系统服务、改系统网络、防火墙、代理或虚拟化设置。

本机既有Linux缺ensurepip，实际用`python3 -m venv --without-pip <server-venv>`，以临时`PYTHONPATH=<官方pip-wheel>`执行该venv的`python -m pip`安装，仅修改此专用环境。bootstrap工具为官方[pip25.3](https://pypi.org/project/pip/25.3/)纯Pythonwheel，SHA-256为`9655943313a94722b7774661c21049070f6bbb0a1516bf02f7c8d5d9201514cd`，不是运行依赖或仓库二进制。运行不保留pip-wheel的PYTHONPATH。可先在已有pip环境从官方PyPI下载wheel及requirements-server.lock依赖，逐文件核验后通过`--no-index --find-links <外部wheelhouse> --require-hashes --only-binary=:all:`安装；本机已实际完成离线安装与Linux运行。没有为缺失ensurepip安装系统包或更改系统Python。

## 实际环境及验证证据

真实独立Linux服务已运行于本机既有WSL2 Ubuntu26.04、内核6.18.33.2、Python3.14.4、SQLite3.46.1，UID/GID1000、有效capabilities为0。仅启动既有发行版，未安装/启用虚拟化。Windows Python3.12.14客户端通过既有localhost转发访问Linux127.0.0.1，不绑定WSL网卡或局域网地址。数据在Linux原生文件系统独立目录；Windows仅HTTP访问，不直接访问其业务SQLite。

真实Windows及Linux HTTP完整链路均验证：全新库及一次bootstrap、强制改密、管理员开户/授权、同名双项目隔离、发布人/时间、四文件13,631,520字节发布及下载哈希、撤权/降级/停用/重置/过期/退出、SQL触发器注入回滚、服务重启及状态/旧会话保持、停止后初始化重放拒绝。Linux额外检查原生大小写、协议大小写冲突、链接拒绝、0600/0644、不可读文件及真实跨进程锁。

另用独立Linux服务真实HTTP验证项目管理授权、改名后UUID保持、只读拒绝修改、来源映射不可变、归档状态禁止发布及恢复active后发布；主动退出后的原令牌立即被拒绝，无凭据访问项目/任务/管理及伪造注册/bootstrap均被拒绝。这些单独验收断言不追加为pytest用例数量。

真实Windows管理窗口对独立Linux服务完成开户、项目授权、成员首次改密、授权列表/版本、发布与下载哈希、停用并拒绝旧会话、重置保持停用、审计与退出。普通成员不显示管理员区，后端也拒绝伪造请求。截图及原始路径仅在仓库外。样本为明确虚构的传输占位，不是CAD/PDF兼容性验收。命名/快照/历史保护含已有编号、作废、预留及计数器的前后比对。

自动化基线809通过；新增定向74通过、0失败/跳过。最终全量**883通过、0失败/错误/跳过，616.29秒**，包含原3A快照、3B备份恢复及3C.1故障/授权矩阵。新增为16项可移植账号逻辑、54项独立参数化HTTP权限场景、完整HTTP重启/回滚1项、可选依赖1项、本地离线启动1项、服务身份替换不泄漏令牌1项。Linux单独21项unittest通过（其中16项账号逻辑与Windows重叠、5项Linux文件系统），不重复计入Windows新增用例数。真实Linux完整HTTP、Windows GUI、资源/CLI检查另记，不把脚本内部断言冒充新增测试用例数。

首轮新增测试曾有68通过/1失败：新启动测试继承offscreen、系统托盘不可用导致等待；修正测试子进程使用Windows平台并断言隔离报告后通过，未修改原托盘检查或删除断言。另实际阻止Python网络连接并模拟Argon2不可导入，Windows隔离Smoke仍显示窗口、生成虚构项目/命名记录，凭据及SOLIDWORKS禁用。旧预览自动化保留；本轮未重复2D长期图形内存研究或宣称STEP内核已安全。

资源：短检查先写预算，不重启服务、不强制GC。首轮120秒、预热20秒，客户端线程在约52秒仅从5降2，因跨度3超过2而未通过；内存/句柄无超预算，原始失败保留。根据线程退休证据将第二轮预热设60秒，仍保持原内存/句柄/线程跨度判据。最终固定代码120.625秒、60轮、零异常；固定一个版本重复项目/版本查询、幂等发布、成员登录/退出、审计，另做四文件下载哈希、管理员CLI及健康检查、归属停止和一致性索引备份quick_check=ok。资源轮中不重启，停止/备份在采样完成之后。

| 预热后指标 | Windows客户端 | Linux服务 |
| --- | ---: | ---: |
| 私有内存跨度MiB | 0.5781 | 0.0625 |
| 拟合趋势MiB/分钟 | +0.1148 | +0.0565 |
| 句柄/FD数 | 247不变 | 6不变 |
| 线程数 | 2不变 | 1不变 |
| 工作集起止约MiB | 39.67→39.61 | 30.19→30.25 |

该Linux进程峰值RSS约92.2MiB、峰值地址空间约261.2MiB。预算为私有跨度≤16MiB、正趋势≤1MiB/分钟、句柄跨度≤4、线程跨度≤2；均满足。只说明一分钟预热后的一分钟有限负载，不外推长期、大规模、6Mbps或2D内存稳定性。Windows启动/停止包装脚本亦已另行启动独立服务核对真实worker PID与健康UUID后停止。

源码与数据保护：1760个受保护旧源码/正式目录文件SHA-256及mtime零变化；237份基线源码副本在另一新目录独立恢复校验通过。所有账号、传输占位、索引、快照、恢复/下载、截图及日志均在仓库外。本轮Windows私有证据根ACL仅保留所有者、SYSTEM及Administrators，Linux原生服务数据目录0700、密码文件0600；未改其他用户目录权限，不宣称数据已加密。

## 尚未验证与停止点

Ubuntu22.04/Python3.10、其他CPU架构、HTTPS/反向代理、公网、正式账号、云服务器资源和NAS均未实际验收；没有将回环HTTP称为安全公网登录。无需本轮创建Nginx配置或系统服务。账号审计按需持久增长、单线程服务与每用户限流适用于当前有限实验，未完成生产留存、抗DoS、密钥托管和配套灾备认证。密码/会话哈希不等于整个数据目录加密，Linux0600及Windows本轮私有目录ACL仅是本次权限检查结果。

下一最小任务建议：另行授权一个Ubuntu22.04隔离环境，复验锁定依赖、同一权限/迁移/HTTP矩阵及服务器配套备份恢复；之后再评审HTTPS与真实服务器容量/回滚。不得据此自动部署腾讯云。Phase2D内存、并发诊断超时及STEP内核补丁仍未关闭。本轮完成后停止。
