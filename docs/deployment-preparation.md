# 本机加固与生产部署准备

独立生产运行包、分离的迁移/运行秘密、TLS模板及关闭注册下的交互建号入口见[deployable-release.md](deployable-release.md)。该包不部署公网，现有实际生产资源/邮件/异地与漏洞门禁仍未通过。

用户已选择先完成本机验收和部署准备，目前没有生产域名/服务器。不要直接把开发 Compose
映射到公网。开放注册是最终需求；邮件验证/找回密码/滥用防护验收前，生产注册须保持关闭。

## 本机资源加固验收

```powershell
./scripts/acceptance.ps1 -Hardened -RestartCheck
```

该覆盖文件在相同隔离 acceptance 项目上增加 CPU/内存/PID 上限、无特权提升、
应用只读根文件系统和有界 tmpfs。Redis noeviction 达限后拒绝写入；任务 DB outbox 保留，
API 限流依赖不可用时拒绝认证请求。不能据此宣称压力测试或生产容量已验收。
不添加公网端口，不删除命名卷。基础文件或 acceptance 命令不带 Hardened 时不会应用这些限制。

## 部署到生产前的配置检查

`APP_ENV=production` 时 API、Worker、Beat 启动自动检查必要配置；也可在运维终端使用：

```powershell
./.venv/Scripts/python.exe scripts/production_preflight.py
```

必须显式配置 HTTPS CORS 来源、TRUSTED_HOSTS、独立运维/下载签名/对象存储秘密、
私有版本化凭证 keyring、认证 Redis，并保证运行环境不持有迁移管理员连接。
当前生产须设置 REGISTRATION_ENABLED=false。检查只读取配置，不触网、不打印密钥；
通过只证明这些配置约束成立，不是发布批准或服务/IAM/TLS 验收。
容器的 environment 需要由部署平台按这组字段显式注入；开发 Compose 的 APP_ENV 固定为
development，不能单靠根目录 .env 改成 production。
Worker/Beat在Celery应用加载时同步校验，不依赖可能吞异常的signal。已通过实际CLI子进程验证
配置不合格时在连接broker前非零退出；合法配置加载不触网。API容器探针显式使用Host: api，
生产TRUSTED_HOSTS需包含api和实际部署域名；内部探针与对外Host设置应一起核验。
运行启动另有真实数据库角色保护：API lifespan、Worker/Beat同步loader检查当前及登录角色，
拒绝super/BYPASSRLS与管理员SET ROLE伪装；错误只输出固定文本，探针结束释放连接池。
`production_preflight.py`仍仅检查配置不联网；不能用它替代启动角色检查或生产服务验收。
公共/S3 URL校验有效主机/端口并拒绝控制字符；Redis仅redis/rediss和明确DB路径，
查询参数不能覆盖地址/认证/数据库；rediss须`ssl_cert_reqs=required`且不能关闭主机名验证。

## 仍需落实的生产配置

本机HTTPS入口已加入，配置、显式CA验证与真实边界检查见[tls-gateway.md](tls-gateway.md)。
它仅绑定回环地址，使用短期测试证书，不能代替真实域名证书/生产网络验收。
S3运行策略、临时STS真实验收与bootstrap凭据分离说明见[storage-runtime-policy.md](storage-runtime-policy.md)。
验收Compose已替换API/Worker/Beat的受限运行身份；生产仍须独立创建/注入受限身份，
不把本机报告视为生产IAM已完成。

1. 选定域名、服务器/平台、数据区域和访问政策；指定 DNS 与证书持有人。
2. 仅 HTTPS 网关公网可达；API/DB/Redis/MinIO/管理端口位于私网。
   网关限制 body 大小/接收期限/连接与请求速率；禁止公开诊断/文档接口。
3. 为数据库和 S3 创建最小权限运行账户，迁移单独执行；Redis 认证/TLS、备份与密钥保管独立。
4. 邮箱验证/密码找回已通过本机真实SMTP和单次消费验收；开放注册前仍须配置生产外发邮件，
   验证SPF/DKIM/DMARC、邮箱送达、故障重发和可信代理下的注册滥用防护。
5. 配置异地加密备份、脱敏审计和监控告警，验证恢复与回滚；记录 RPO/RTO。
6. 镜像漏洞扫描、固定 digest、供应链/MinIO 许可审查与独立安全评审。

本机已将 Python、Node、PostgreSQL/pgvector、Redis 与 Nginx 的运行引用固定到 SHA-256 digest，
并用 Docker Registry 官方 manifest 校验当前 digest；`scripts/check_image_pins.py` 会在统一检查中拒绝
可变运行 tag。用户已授权Docker Scout仅发送PURL与镜像层digest；当前已为8个本机运行镜像生成
`.cache/scout-reports/`中的本地SPDX SBOM。2026-11-07 已在用户授权范围内对当前 11 个本机镜像完成
Docker Scout CVE 匹配并生成 `.cache/scout-reports/20261107/` 下的 SARIF；报告存在 error/warning
级漏洞，故漏洞门禁失败，不能把扫描结果当作通过。修复并重新构建实际镜像后必须重新扫描；供应链、许可与
独立安全评审仍需单独完成。

Python安装现使用`requirements/runtime.lock`、`dev.lock`、`build.lock`三份强制SHA-256锁，
原`requirements.lock`只保留版本约束来源。支持CPython3.12 Windows amd64与Linux glibc>=2.28 amd64；
pip拒绝源码包和无hash工件。项目wheel在固定构建后端的独立阶段禁网构建，运行阶段不含开发/构建工具。
128个wheel已对照官方PyPI摘要；Windows全新离线安装/真实篡改包拒绝、Linux无缓存构建均通过。
这只证明工件完整性和构建边界，CVE匹配、许可、签名和独立安全评审仍需完成。

运行镜像修复与最新Image ID绑定的真实复扫见[运行镜像漏洞验收](runtime-image-security.md)及PROGRESS.md顶部。
重建镜像不能继承旧SARIF的结论；外部资源缺失与未修复漏洞继续阻止公开上线。
锁重生成工具依赖Git忽略的本机wheel缓存，不应作为新checkout默认检查。

API 请求日志只含随机请求 ID、路由模板、方法、状态与耗时。关闭原始 Uvicorn access log，
避免查询字符串里的短期下载票据进入日志；不信任任意 X-Forwarded-For。
目前 BFF 使后端按 BFF 地址限速，生产需要经过核验的网关客户端限流方案。

## 业务审计范围

数据库审计覆盖空间、成员、任务、文件与模型配置/偏好的关键变更，与业务同事务提交。
管理员在账户页按游标读取；无内容/秘密字段，运行角色禁止更新和删除历史。
全局身份事件已另由0010及自身账户安全历史覆盖，见[account-security-history.md](account-security-history.md)。
认证限流前拒绝、全部失败恢复链接、独立审计归档、保留/删除政策和告警投递仍待补齐。
新迁移上线前需备份并执行追加迁移；审计保留不支持直接降级删除。
本机受权只读快照导出、加密与离线校验见[audit-archive.md](audit-archive.md)；
同机归档不替代独立防篡改存储、密钥托管或保留/删除政策。
