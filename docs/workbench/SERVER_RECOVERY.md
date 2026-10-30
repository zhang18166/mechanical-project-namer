# 服务端完整备份与替代实例恢复

仅用于隔离服务，Python入口为`mechanical_project_namer.server_recovery`；不打开桌面业务库。只做完整未加密目录集，不做增量、压缩、轮换、定时、加密或覆盖恢复。需要可信备份、私有文件权限和外部安全变更记录，SHA-256不是签名或磁盘防篡改保证。

## 范围和一致性

格式1包含`manifest.json`、`COMPLETE.json`、`data/index.sqlite`、`data/config.json`及正式`storage`文件。数据库保留用户UUID/Argon2哈希/角色/启停、项目UUID/来源映射/授权、完整和终结任务、清单、发布人及审计；服务UUID和所有权标记配套保存。源文件/STEP/GLB/PDF等角色、中文相对路径、大小和哈希原样保留，不解析CAD。

排除明文密码文件、原始会话令牌、日志、缓存、宿主或客户端工作文件以及已终结上传暂存副本；任务和失败/取消历史在数据库保留。遇receiving等未完成任务直接拒绝：先通过原协议核对后继续或明确取消，再停服重试，备份程序不自动提交。完整备份含会话摘要，恢复副本必须全部撤销。

停服后取得逻辑服务租约和索引独占锁，并用SQLite BEGIN IMMEDIATE拒绝未协调写入；SQLite在线backup取得一致副本，分块复制正式文件。检查数据库结构/外键、服务UUID、项目映射、任务绑定、所有权、正式目录集合、清单和全部文件哈希。不是仅运行integrity_check。校验SQLite时用临时受控副本，校验/恢复不改备份本体。

单文件仍遵守原发布协议16MiB/版本64MiB；实验服务256MiB仓库容量未扩大。备份额外约束索引64MiB、最多30000项、目录集总量512MiB、JSON元数据2MiB并预留64MiB空间，任一超限拒绝。拒绝链接、硬链接、路径越界/设备/ADS、大小写/Unicode冲突及异常额外文件/目录。数据库超过限制时明确失败，不自动截断审计。

## 状态与恢复边界

任务UUID绑定来源、目标及冻结指纹。状态为prepared→frozen→copying→sealed→complete；failed/cancelled与原因保留在仓库外任务台账。暂存与最终目录同一文件系统，使用排他rename，不能用SQLite事务假装文件系统原子提交。清单、完整标志及全部内容核验完成后才提交目录。

同任务最多两个独立复制尝试；保留失败产物，超过上限安全拒绝，要求人工检查并明确新建任务，不自动堆积无限重试。封存后重启可验证并提交；目录已提交但台账未记成功时，只有任务/清单/完整内容一致才能补登记。未知目录拒绝接管，不自动清空；不删除历史版本。完成任务重试返回原已冻结结果，需要新备份点时明确新任务/新目标。取消仅在提交前生效，提交后不删除成功产物。

恢复只写不存在的新目录。先验证完整备份，再复制索引及所有正式文件；仅新配置的index/storage/lease_root绑定新路径。逻辑服务UUID不变，新运行实例UUID不同。恢复审计和门禁位于服务schema3，旧schema2加载器拒绝打开，不能降级回4A旧服务运行恢复副本。桌面Schema13不变。

服务schema2→3升级前做SQLite一致性副本，增量元数据与版本更新同事务，可重复、失败回滚；账户/授权/版本等原业务记录不改。服务身份写入索引，错配拒绝。SQLite热回滚日志需由现存库`mode=rw`连接先恢复，再读版本；不会用创建空库代替缺失索引。新程序在已有仓库索引缺失时拒绝自动重建账号体系。

恢复后删除全部会话摘要，保留初始化标记并追加恢复审计。启动时处于维护状态：普通账号拒绝登录/请求；管理员只可读元数据和审计，HTTP开户、重置、授权、改密、发布及下载均拒绝。Windows界面显示维护状态并关闭写按钮。**放行只通过停服后的本地维护CLI，不提供匿名HTTP恢复、默认口令或万能密码。**

## 命令

以下路径为占位符，所有目录须私有且仓库外。完整备份不是桌面3B备份，也不是`backup-index`。

```sh
# 先正常停止本轮测试服务，再使用全新的备份目标及UUID。
<venv>/bin/python -m mechanical_project_namer.server_recovery backup --config <服务根>/server.json --target <新备份目录> --task <备份任务UUID>
<venv>/bin/python -m mechanical_project_namer.server_recovery verify --backup <备份目录>

# 旧服务停止且不可访问；恢复入口只依赖备份，不依赖旧数据库。
<venv>/bin/python -m mechanical_project_namer.server_recovery restore --backup <备份目录> --target <新替代实例根> --task <恢复任务UUID> --lease-root <同机原逻辑服务租约根>
<venv>/bin/python -m mechanical_project_namer.server_recovery inspect --config <新替代实例根>/server.json
<venv>/bin/python -m mechanical_project_namer.server_recovery approve --config <新替代实例根>/server.json --username <恢复点管理员> --review-file <私有审核.json>
```

approve的管理员密码由getpass或0600的`--password-file`输入，不进入URL/日志/业务台账。审核文件也必须为该Linux用户所有、无组/其他用户权限。Windows证据目录ACL需单独限制并核验，文件不入Git不等于已加密。

同机源服务和替代实例必须使用同一个私有lease_root，逻辑服务UUID锁阻止两位置同时启动；另有索引锁保护同一库。这不是跨主机分布式防双写。跨机器恢复必须由管理员隔离旧实例并核对路由，不能随意启动两个相同逻辑身份的生产服务。本轮没有自动启动后台服务或对外监听。

## 安全状态回滚审核

备份后的停用、撤权、改密不会自然出现在旧备份。必须根据可信的后续记录逐用户逐项目核对；不能宣称恢复点包含未来安全事件。审核需要显式确认旧服务离线和安全变更已核对，列出**全部用户及全部项目**的最终角色、启停和授权（无权限也写空列表）。所有启用的普通成员强制设置新的初始密码，放行后首次登录再修改；停用和撤权必须按已知后续状态保留。

审核结构示意（UUID/路径均替换为inspect输出，不能直接照抄默认账号）：

```json
{
  "recovery": "<当前恢复UUID>",
  "source_offline": true,
  "security_changes_reviewed": true,
  "users": [
    {"id":"<管理员UUID>","enabled":true,"role":"admin","password_action":"keep-confirmed","projects":{"<项目UUID>":["metadata_view"]}},
    {"id":"<成员UUID>","enabled":true,"role":"designer","password_action":"reset","password_file":"<私有新初始密码文件>","projects":{"<项目UUID>":["metadata_view"]}}
  ]
}
```

实际必须覆盖所有用户和项目，不能遗漏或重复。管理员若处于必须改初始密码状态，亦须在本地审核中reset。最后一个有效管理员受保护。密码重置、授权替换、撤销会话、审核审计和门禁放行同一SQLite事务；提交前崩溃保持关闭，提交后重放拒绝重复审核。备份和原服务不受这些修改影响。

放行后重新启动新实例，客户端重新登录并核对服务UUID/当前授权/版本及文件哈希；先前任何令牌都不应复活。公开文档不保存审核文件、密码哈希数据库、快照或原始路径。实际证据见[PHASE4B](PHASE4B.md)。
