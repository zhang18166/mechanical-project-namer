# Phase4B目标组合与复现

本轮实际组合：Ubuntu22.04.5 LTS / WSL2、x86_64、glibc2.35、独立CPython3.12.16、SQLite3.53.1、Python ssl链接OpenSSL3.5.9。内核为宿主提供的6.18.33.2-microsoft-standard-WSL2，不冒充裸机Ubuntu通用内核。服务UID/GID1000、仅该普通用户组、有效capabilities为0；umask0077、服务地址空间512MiB、FD128、只监听127.0.0.1。

采用仍处于[安全维护期的Python3.12分支](https://devguide.python.org/versions/)，没有把Ubuntu22.04等同于Python3.10。本轮没有验证Python3.10，没有替换系统Python。原Ubuntu26.04发行版和Windows默认发行版保留。

## 来源与校验

| 资源 | 固定来源与SHA-256 |
| --- | --- |
| Ubuntu22.04官方WSL amd64 rootfs | [Canonical源](https://cloud-images.ubuntu.com/wsl/jammy/current/ubuntu-jammy-wsl-amd64-ubuntu22.04lts.rootfs.tar.gz)，[官方SHA256SUMS](https://cloud-images.ubuntu.com/wsl/jammy/current/SHA256SUMS)；`1483cc5c1dce13064f774834cbffdff226559fd522a67a381a8ea77d63fb4109` |
| 独立CPython3.12.16 | [Astral python-build-standalone发布20261026](https://github.com/astral-sh/python-build-standalone/releases/tag/20261026)，资产`cpython-3.12.16+20261026-x86_64-unknown-linux-gnu-install_only.tar.gz`；`6a79e922e50006b601f29e688407659da1c2b86ebcd4e9c7254cf234be8b89de` |

下载后实际计算SHA-256并核对官方HTTPS清单/发布资产digest；没有声称完成独立GPG或构建可重复性认证。Ubuntu镜像文件日期为2025-03-18，属于明确的旧快照，本轮未做全系统更新或生产补丁认证；Python的OpenSSL版本不代表所有发行版软件包已更新。镜像、解释器、wheel与VHD仅留仓库外，不随源码分发。

运行依赖沿用`requirements-server.lock`中的Argon2及版本/哈希，不降级依赖。Linux CPython3.12 cffi wheel的已有哈希本轮已实际验证安装；锁文件头的“未实测”是4A历史，4B结果以本文为准。许可及来源见[账号组件记录](../../third_party/accounts/README.md)。解释器自带CPython/第三方许可保留在专用运行时目录，参见[独立构建说明](https://docs.astral.sh/uv/concepts/python-versions/#managed-and-system-python-installations)。

## 安全复现边界

先按[Microsoft导入文档](https://learn.microsoft.com/en-us/windows/wsl/use-custom-distro)核实现有WSL2可用；缺失时停止，不自动安装或启用虚拟化。下面是人工命令结构，路径均须替换为新建私有目录；不得覆盖已有发行版或目录。不要设置默认发行版，不执行宿主重启或全局代理/网络修改。

```powershell
wsl --list --verbose
# rootfs及Python归档必须先按上表校验；目标不存在时才能导入。
wsl --import <专用测试发行版> <新私有VHD目录> <已校验rootfs.tar.gz> --version 2
# 仅新测试实例的初始化需要root；普通测试用户不设默认密码、不授sudo。
wsl -d <专用测试发行版> --exec /usr/sbin/useradd --create-home --shell /bin/bash <测试运行用户>
wsl -d <专用测试发行版> --exec /usr/bin/install -d -m 700 -o <测试运行用户> -g <测试运行用户> <Linux私有根>
wsl -d <专用测试发行版> -u <测试运行用户> --exec /usr/bin/tar -xzf <已校验Python归档> -C <Linux私有根>
wsl -d <专用测试发行版> -u <测试运行用户> --exec <Linux私有根>/python/bin/python3 -m venv <Linux私有根>/venv
```

本机导入实例的PATH没有找到`useradd`，首次命令明确失败，改用已有`/usr/sbin/useradd`后成功；没有修改系统PATH或安装系统包。所有后续服务与维护调用显式`-u <测试运行用户>`。解释器及venv在用户私有根，系统Python保持原样。依赖安装使用官方wheel、`--no-index --find-links <已核验wheelhouse> --require-hashes --only-binary=:all: -r <repo>/requirements-server.lock`，只写此venv。

## 运行与测试

```sh
export PYTHONPATH=<本轮公开源码工作树>
<venv>/bin/python -m mechanical_project_namer.account_server init --root <新服务根> --config <新服务根>/server.json --lease-root <同机私有租约根>
<venv>/bin/python -m mechanical_project_namer.account_server bootstrap --config <服务根>/server.json --username <虚构管理员>
<venv>/bin/python -m mechanical_project_namer.account_server serve --config <服务根>/server.json --ready <新的私有ready.json>
<venv>/bin/python -m mechanical_project_namer.account_server stop --ready <私有ready.json>
```

bootstrap通过getpass，或所有者0600的仓库外测试密码文件输入；不把密码写进命令行。ready每次使用新文件。客户端公共连接JSON仅含`port`和`service`，用现有WSL localhost转发连接127.0.0.1，不使用WSL网卡地址，不增加全局端口转发。实际Windows客户端通过HTTP联调，未使用Linux内部mock代替。

可移植及Linux专用测试分开执行：

```sh
PYTHONPATH=<repo>:<repo>/tests <venv>/bin/python -m unittest portable_accounts portable_linux portable_server_backups portable_server_faults -v
```

没有将Windows COM/PyQt专用用例大批skip后称Linux全量通过。Windows真实GUI/协议及资源脚本使用已有测试夹具，可通过该进程的`MW_ACCOUNT_TEST_DISTRO`、`MW_ACCOUNT_TEST_USER`、`MW_ACCOUNT_TEST_ROOT`、`MW_ACCOUNT_TEST_PYTHON`指定专用实例；默认值保留4A历史。运行产物必须位于新仓库外目录。

```powershell
.\scripts\dev_accounts.ps1
.\scripts\test_workbench.ps1
.\.venv\Scripts\python.exe -m scripts.verify_server_recovery --output <新私有证据目录>
.\.venv\Scripts\python.exe -m scripts.verify_server_resources --output <另一新私有证据目录> --seconds 1800
```

后两项需要上述Linux选择变量和已核验的独立环境。资源测试禁止缩短至30分钟以下，先写预算、固定数据、记录实际PID及代码指纹；与故障恢复测试分别统计。WSL同机演练不代表异地、坏盘、断电、裸机或腾讯云验收。结果见[PHASE4B](PHASE4B.md)。
