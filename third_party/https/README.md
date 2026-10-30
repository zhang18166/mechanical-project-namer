# HTTPS隔离入口第三方记录

固定来源、包SHA-256及签名指纹见[sources.json](sources.json)，Python wheels逐项固定在`requirements-https.lock`和仅测试用`requirements-tls-test.lock`。只从官方PyPI、nginx.org和Ubuntu security archive取得；没有把deb、wheel、镜像或密钥提交。现有Argon2依赖与许可证沿用`third_party/accounts`，没有替换密码算法。

2026-10-30实际检查了Nginx官方安全公告，选择1.30.6稳定版，覆盖公告中1.30.6修复边界；没有启用HTTP/2、HTTP/3、AIO、DAV、动态模块或静态仓库映射。Uvicorn选0.54.0，使用标准asyncio及h11 0.16.0，关闭WebSocket、HTTP2、自动代理头信任和多worker；h11此前宽松分块解析问题的修复版本边界为0.16.0。**没有把pip/npm audit的零报告当作安全证明。**

Nginx官方Jammy deb仅用`dpkg-deb -x`解包到普通用户私有prefix，未运行安装/维护脚本或改系统Nginx。官方Release的GPG签名、Packages SHA-256和deb SHA-256均实际核验；官网通过HTTPS取得的签名公钥不等于本机建立了额外Web-of-Trust。Linux旧镜像自带libssl3 3.0.2-0ubuntu1.19，未直接继续用它做本轮TLS：Ubuntu签名InRelease→Packages.gz→3.0.2-0ubuntu1.30 deb完整性链已核验，在另一个私有目录解包，仅Nginx子进程的LD_LIBRARY_PATH指向该目录；没有改系统动态链接配置。实际加载文件另以/proc映射核对。此选择不代表整份Ubuntu镜像或所有系统库已完成全面安全更新。

Uvicorn及Click为BSD-3-Clause，h11为MIT，Nginx为BSD-2-Clause，OpenSSL3为Apache-2.0，测试证书工具cryptography为Apache-2.0或BSD-3-Clause双许可，均保留原版权和许可证文本，与本仓库GPL-3.0-only使用方式兼容。没有新增原生CAD DLL。cryptography只生成仓库外测试证书，服务和桌面基础启动均不导入它。

维护与行为依据：

- [Uvicorn发布记录](https://uvicorn.dev/release-notes/)、[流控和资源行为](https://uvicorn.dev/server-behavior/)、[安全报告入口](https://github.com/Kludex/uvicorn/security)。
- [h11安全公告](https://github.com/python-hyper/h11/security/advisories)。
- [Nginx官方下载](https://nginx.org/en/download.html)、[Linux包及签名](https://nginx.org/en/linux_packages.html)、[安全公告](https://nginx.org/en/security_advisories.html)。
- [Nginx流式请求/响应配置](https://nginx.org/en/docs/http/ngx_http_proxy_module.html)、[空闲时限语义](https://nginx.org/en/docs/http/ngx_http_core_module.html#send_timeout)。`send_timeout`是两次写之间的时限，不宣传为整条下载连接的绝对总寿命。

重现入口见[HTTPS隔离运行](../../docs/workbench/HTTPS_ISOLATION.md)。用户目录安装脚本固定校验上述制品，不自动追随latest；后续版本须重新查公告、更新锁和完整验收。私有CA不是公网证书签发、续期、吊销或生产合规认证。
