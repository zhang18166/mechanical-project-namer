# HTTPS隔离运行与协议边界

仅在现有专用Ubuntu22.04测试环境、普通用户和仓库外私有目录运行。不是腾讯云部署模板；不修改系统Nginx、WSL默认发行版、hosts、根证书、代理或防火墙。应用1.7.1、桌面Schema13、服务账号schema3不变。依赖版本、哈希和许可证见[第三方记录](../../third_party/https/README.md)。

## 准备

下面均为占位路径，不可照抄成真实生产目标。工作树以`git rev-parse --show-toplevel`、`git worktree list`的实际结果为准，路径段为`<开发根>/_worktrees/mechanical-workbench-phase4c-https`。

在Windows独立venv安装原锁定桌面依赖；测试证书工具另行安装，不是基础命名功能的依赖：

```powershell
.\.venv\Scripts\python.exe -m pip install --require-hashes --only-binary=:all: -r requirements-tls-test.lock
.\.venv\Scripts\python.exe -m scripts.make_tls_fixtures --output <仓库外私有测试证书目录>
```

生成7天测试CA及有效、替换、错误主机、过期、尚未生效的叶证书。模拟有效期不修改电脑时间。测试CA私钥仅保留在生成目录，不发给客户端或Linux服务；Linux服务只需要相应叶证书、叶私钥和CA公开证书。Windows证据根应限制所有者/SYSTEM/Administrators并独立检查ACL；Linux目录0700、密钥0600。它们没有加密，不得入Git。

在已经验证的独立Python3.12.16环境、源工作树目录中准备一个**全新用户prefix**：

```sh
<独立Python>/bin/python3 -m scripts.prepare_https_dependencies \
  --root <新私有HTTP环境> --archives <私有制品目录>
# 已有本轮环境只校验，不重新安装或改动系统：
<私有HTTP环境>/venv/bin/python -m scripts.prepare_https_dependencies \
  --root <已有私有HTTP环境> --archives <已核验制品目录> --verify-only
```

脚本固定校验nginx.org/Ubuntu制品SHA-256，只`dpkg-deb -x`解包，不安装deb、运行维护脚本或注册服务。venv仅从官方PyPI安装哈希锁；制品和venv均不提交。本轮实际还核验了官方仓库的签名与制品索引校验链。Nginx只在子进程环境中加载独立的Ubuntu补丁版libssl3；不写全局LD_LIBRARY_PATH或动态链接设置。

## 初始化、启动、检查和停止

```sh
# 仅创建虚构测试实例。首次管理员一次性本地bootstrap，密码由getpass输入。
<venv>/bin/python -m mechanical_project_namer.account_server init \
  --root <新服务数据根> --config <新服务数据根>/server.json
<venv>/bin/python -m mechanical_project_namer.account_server bootstrap \
  --config <服务数据根>/server.json --username <虚构管理名>

# 服务库与HTTPS运行prefix分开，后者必须是新的私有目录。
<venv>/bin/python -m scripts.https_runtime prepare \
  --runtime <新HTTPS运行目录> --config <服务数据根>/server.json \
  --nginx <HTTP环境>/nginx/usr/sbin/nginx --port <未占用回环高端口> \
  --cert <Linux私有叶证书.pem> --key <Linux私有叶私钥.key> \
  --ca <Linux CA公开证书.pem> \
  --tls-library-dir <HTTP环境>/openssl/usr/lib/x86_64-linux-gnu
<venv>/bin/python -m scripts.https_runtime run --runtime <HTTPS运行目录>

# 在另一受控终端执行：
<venv>/bin/python -m scripts.https_runtime check --runtime <HTTPS运行目录>
<venv>/bin/python -m scripts.https_runtime stop --runtime <HTTPS运行目录>
```

从WSL外调用时显式指定专用发行版和普通用户，并在进程级传`PYTHONPATH=<工作树Linux路径>`；不更改默认发行版。`run`是前台自建实例，不是系统服务。只有Nginx监听127.0.0.1高端口，应用仅监听0700目录下的0600 Unix socket。启动取得既有服务UUID租约及索引独占锁。ready保存真实应用/代理/监督PID与实例身份；停止核对UID、命令行中的独立运行目录，等待有界退出，不按进程名全局杀程序。崩溃后新建运行prefix，旧目录保留，既有数据库和发布任务通过原协议恢复，不自动接管未知残留socket。

`check`表示进程及逻辑服务身份可达，不表示所有版本健康或维护门禁已放行。恢复服务仍必须按[4B本地审核](SERVER_RECOVERY.md)放行。普通用户关闭、管理员HTTP只读，TLS可达也不能绕过。

## Windows客户端

```powershell
.\scripts\dev_accounts.ps1 -DevRoot <仓库外新开发客户端目录>
# 独立Smoke（不需服务端登录）：
.\scripts\dev_accounts.ps1 -DevRoot <仓库外另一个新目录> -Smoke
```

在“项目文件→隔离测试账号与远端项目”选择仓库外连接JSON，使用虚构账号。新格式为：

```json
{
  "url": "https://127.0.0.1:<回环高端口>",
  "service": "<期望服务UUID>",
  "ca": "<Windows本项目测试CA公开证书.pem>"
}
```

配置不含密码或令牌，不含CA私钥。当前监听Host固定127.0.0.1及该端口，证书含127.0.0.1 SAN。SSLContext只加载所选CA，保持CERT_REQUIRED、主机名校验和TLS1.2最低版本；TLS1.3实际协商结果见PHASE4C。先做真实握手，再在**同一个前端TLS连接**进行公开health/UUID确认，之后才发密码/令牌/内容；拒绝响应不跟随跳转、不回退HTTP、不使用系统代理转发本机实验请求。

切换地址、UUID或账号需新会话；运行中改连接字典会清除会话并拒绝请求。HTTPS发布台账按规范化origin、CA内容指纹、服务UUID、账号UUID、远端项目、本地store/version隔离。没有改桌面数据库，台账仍为实例私有`.publish-state`，不进入3B备份；恢复后仍需核对远端。历史确认时间保留，当前连接失败显示操作未完成，旧列表不能作为刚核验健康的证明。

窗口单独显示所选目标地址/UUID、当前操作是否确认及上次确认时间；“已确认”不表示持续在线。开始新操作或发生失败时取消当前确认，历史版本仍保留并标为“上次查询”。网络恢复后必须重新进行经过TLS/UUID校验的操作；不会仅因进程ready或保留旧列表就恢复当前确认。

原`port/service`纯回环HTTP格式仅在显式开发profile或测试进程`MW_ALLOW_ISOLATED_HTTP=1`兼容；这是独立历史测试路线，不是HTTPS失败备用。新入口没有运行或转发旧HTTPServer。令牌仅内存保存；退出、停用、改密及角色/授权变更仍由原服务实时判断。

## 并发、流控和容量

`AccountService/PublishService`为共享同步业务操作，HTTP和ASGI仅提供传输原语；没有复制认证、编号、项目、任务或提交状态机。ASGI通过单线程有界执行器做复制/哈希/Argon2，事件循环不执行这些阻塞工作。**显式只准入一个业务操作、排队0，忙时503**；不是误以为单worker天然串行。工作线程在接收请求前启动，启动失败不中途接收写任务；构造/调度异常不能留下永久忙碌标志。Uvicorn连接上限8、backlog8；Nginx单进程事件模型，每地址最多4个已读完头的活动请求，空闲/握手连接还受worker_connections64、头等待及FD128约束，不能把limit_conn解释成包括一切空闲连接；入口64请求/秒加32突发，进程地址空间512MiB。Argon2仍64MiB/t3/p4、每次最多一个密码计算；原账号失败限流不能用转发头绕过。

请求取消不等于线程停止：业务准入只由实际工作线程完成回调释放，重复取消也不能提前放行下一写操作。上传中管理/取消请求可能忙时拒绝，应待当前有界操作结束后重试；已接受的撤权会在后续请求及最终commit再判断。文件传输中断可继续同任务，显式取消任务不得自动复活，已经提交的版本不删除。不会承诺收回已经发送的文件字节。

Nginx上传和下载缓冲均off，proxy临时文件上限0、共享缓存off、非幂等上游自动重试off。应用按64KiB流式读写和哈希；单文件16MiB、版本64MiB、100文件及仓库256MiB不扩大。JSON一般8KiB、任务清单2MiB，响应JSON2MiB，头部2×8KiB、头等待5秒。应用上传空闲≤10秒/总量≤30秒、账号JSON总量5秒；客户端上传/下载总量30秒，JSON响应总量10秒；代理空闲读写10秒、上游读35秒。服务索引及WAL达到64MiB后拒绝新业务并要求维护，不清空审计；单请求可能产生小幅越界，非文件系统硬配额。访问日志和代理错误日志均关闭，不记录请求体/凭据/私有路径；业务安全审计保留在私有索引，容量不足受控停止。

Host、方法、Origin明确限制，Nginx移除伪造Forwarded/X-Forwarded-For/X-Real-IP/转发Host后设置固定https；应用不启用Uvicorn代理头信任。信任来自私有socket及同UID私有目录，不把任意本机连接当身份，不宣称隔离同一OS账号或本机管理员。所有文件仍经授权及正式版本健康检查，没有静态存储路由。恢复备份仍只取4B白名单，HTTPS私钥、CA、日志和运行配置不混入备份。

**未完成绝对前端慢连接总寿命证明。** Nginx send_timeout/client_body_timeout是空闲间隔，不能当作恶意接收者整条连接的绝对时限；应用和受控客户端的30秒也不能自动证明代理已把末端连接关闭。当前连接/内存受上述有界限制，但这项不能标PASS，尚不能作为公网入口放行。

## 验收命令

所有输出必须仓库外。真实HTTP脚本要求显式指定专用环境变量`MW_ACCOUNT_TEST_DISTRO/USER/ROOT/PYTHON`及`MW_HTTPS_TEST_CERTIFICATES/NGINX/LIBDIR`；值为本机私有环境，不写入公开配置。变量仅传当前验收进程。

```powershell
.\scripts\test_workbench.ps1
.\.venv\Scripts\python.exe -m scripts.verify_https_gui --output <新GUI证据目录>
.\.venv\Scripts\python.exe -m scripts.verify_https_gui --negative --output <新TLS拒绝GUI证据目录>
.\.venv\Scripts\python.exe -m scripts.verify_https_gui --confirmation --output <新历史确认GUI证据目录>
.\.venv\Scripts\python.exe -m scripts.verify_https_transfers --case gui --output <新断流GUI证据目录>
.\.venv\Scripts\python.exe -m scripts.verify_https_recovery --output <新整体恢复证据目录>
.\.venv\Scripts\python.exe -m scripts.verify_https_resources --seconds 1800 --output <新资源证据目录>
```

`--control`只在隔离验收监督进程明确提供时传给`asgi_server --isolated-test`；正常运行省略，客户端HTTP没有故障配置API。硬退出、存储错误和磁盘空间故障只作用于本轮自建进程/虚构目录，不能称断电认证。详见[本轮结果](PHASE4C.md)。
