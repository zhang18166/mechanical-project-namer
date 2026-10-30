# 账号服务第三方依赖

2026-10-30从官方PyPI取得，不捆绑wheel、原生库、虚拟环境或安装包。固定哈希见根目录`requirements-server.lock`；已下载并校验的6个wheel来源文件名、SHA-256及保留的原始许可映射见[wheels.json](wheels.json)。Windows CPython3.12和Linux CPython3.14实际安装通过。额外保留Linux CPython3.10/3.12的cffi官方wheel哈希以供Ubuntu22.04兼容验证，这两种Linux解释器尚未实测。

| 包 | 版本 | 用途 | 许可 / 官方来源 |
| --- | --- | --- | --- |
| argon2-cffi | 25.1.0 | 密码哈希及验证 | MIT；[发布页](https://pypi.org/project/argon2-cffi/25.1.0/)、[API](https://argon2-cffi.readthedocs.io/en/stable/api.html) |
| argon2-cffi-bindings | 25.1.0 | Argon2原生绑定 | MIT及wheel原文中的上游归属；[发布页](https://pypi.org/project/argon2-cffi-bindings/25.1.0/) |
| cffi | 2.0.0 | 绑定支持 | MIT；[发布页](https://pypi.org/project/cffi/2.0.0/) |
| pycparser | 2.23 | cffi依赖 | BSD-3-Clause；[发布页](https://pypi.org/project/pycparser/2.23/) |

本目录保留wheel中的完整原文，Windows/Linux绑定许可存在字节差异，分别保留。PyQt及已有预览依赖未升级，继续使用[原记录](../preview/README.md)。服务器运行不导入这些桌面/预览组件；客户端账号API不依赖Argon2。

使用高层PasswordHasher，固定Argon2id、64MiB、3次迭代、4并行、随机16字节盐和32字节输出；成功登录按参数检查是否需要重哈希。没有自创加密算法。应用仍使用GPL-3.0-only，第三方保留各自条款。

SHA-256锁定用于来源与字节一致性，不等于无漏洞证明。没有以npm/pip审计数量代替安全结论。仅回环HTTP，当前未获得HTTPS、公网、Ubuntu22.04或生产认证；安装前须按目标平台重新审查依赖与官方安全公告。
