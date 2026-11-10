# 本机 HTTPS 网关验收

此模块为既有隔离验收栈增加 Nginx HTTPS 入口，仅绑定本机14080/14443。
镜像从固定官方digest构建，移除没有配置消费者的动态模块与XML库，并精确安装zlib修复版；
不安装系统工具/信任根，不配置DNS/ACME，不公开部署。

```powershell
# 仅首次：生成7天本机证书；已有目录非空时拒绝覆盖
./.venv/Scripts/python.exe scripts/tls_gateway.py --prepare
docker compose --env-file .env.acceptance --env-file .env.storage-runtime.acceptance -p knowledge-os-acceptance -f docker-compose.yml -f docker-compose.acceptance.yml -f docker-compose.storage-runtime.yml -f docker-compose.hardened.yml -f docker-compose.gateway.yml up -d --no-deps gateway
./.venv/Scripts/python.exe scripts/tls_gateway.py --acceptance
```

证书位于Git忽略的.cache/tls-gateway。临时CA私钥生成后丢弃，服务私钥仅供本机测试容器读取；
它不是生产密钥托管方案。验收客户端显式信任该CA并核验localhost证书，不关闭证书验证。
浏览器默认不会信任此测试证书；不要把它导入系统信任库。
证书到期后由操作者保留/移走旧测试文件，重新生成并重建网关，不自动覆盖现有密钥。

HTTPS根路径跳转/account，工程诊断控制台仍可从原本机13000访问。
网关封锁/api/system、/health、/docs、/redoc、/openapi.json、/metrics与直连/api/v1，
只通过Next.js同源账户代理使用业务API。原API/DB/Redis/MinIO的验收端口仍限本机，
此覆盖不删除原端口，不能直接照搬为公网生产网络拓扑。

网关用真实连接地址限流，忽略用户X-Forwarded-For，不使用real_ip配置；认证路径1次/秒、
允许5次突发，其余账户API20次/秒，最多20个并发连接。10MiB请求体限制、10秒头部/体积间隔超时、
140秒上游读取间隔超时、70秒上游发送间隔超时与现有API/BFF总期限共同生效。
Nginx的间隔超时不等于整体请求时限。
本机Docker地址转发不证明生产多用户客户端IP方案已验收；多级代理须重新核验信任链与限额。

禁访问日志，错误日志仅crit级，避免下载query票据、认证头和邮件链接写入网关日志。
响应加nosniff/no-referrer/禁止嵌入；测试域名不加HSTS。HTTP重定向只用固定HTTPS来源，
丢弃路径和query，避免反射Host/票据；带敏感内容的请求应直接使用HTTPS。

真实验收分别强制TLS1.2/TLS1.3握手并核验证书链与主机名，任一协议不符或握手失败即失败；
同时检查账号页面、匿名BFF拒绝、7条诊断/直连路径封锁、
错误Host、超体积请求、伪造转发IP下的429与固定HTTP重定向；检查容器非root、只读根、
资源限制、固定本机端口与nginx -t。脱敏证据保存在.cache/gateway-reports/<ID>/summary.json，
production_tls_verified始终false。

网关镜像由`infrastructure/docker/gateway.Dockerfile`构建，固定官方Nginx1.30.6基础manifest，
删除未加载的xslt/njs/image-filter/geoip/acme动态模块，apk实际卸载无其他消费者的libxml2/libxslt及图像库。
保留包数据库与Nginx核心HTTP/proxy/SSL/限流功能；zlib精确1.4-r0。非root用户101、只读根与回环端口不变。
Dockerfile与Compose来源由镜像摘要守卫检查，运行镜像ID及真实漏洞报告见PROGRESS.md。
不能因标签manifest更新就假定漏洞已修复；本轮官方候选曾与原Nginx运行Image ID完全相同。

生产仍需域名/平台/区域、受信证书及续期告警、HSTS策略、私有网络与管理入口、真实代理IP验证、
SMTP送达、最小权限存储账户和安全评审。生产注册保持关闭，配置通过不等于可以公开上线。
