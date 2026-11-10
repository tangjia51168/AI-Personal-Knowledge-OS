# 邮箱验证与密码找回

账户页支持请求验证邮件、申请重置密码、手动确认单次链接。邮件使用固定模板纯文本，
链接令牌放在 fragment；页面挂载后移除 fragment，仅在内存保留，确认后 POST 提交。
不通过 GET 消费令牌，也不将令牌写入 localStorage、Cookie、队列或日志。

0007_auth_recovery 增加 users.email_verified（既有账户默认未验证）和全局认证索引
auth_action_tokens。只存32随机字节令牌的SHA-256，绑定用户/用途；验证30分钟、重置15分钟，
同用途重发作废旧链接。消费先锁User再刷新令牌，成功后删除；重置密码同时删除所有会话与操作链接。
登录先锁User再验证密码，与重置串行，旧密码不能在重置提交后新建会话。

EMAIL_VERIFICATION_REQUIRED默认false，保持本机开发流程；设为true后注册不签发会话，
未验证用户登录/API/Worker均被拒。不要把既有账户自动标为已验证；须实际完成验证。

SMTP由运维配置SMTP_HOST/PORT/USERNAME/PASSWORD/FROM/SECURITY，PUBLIC_APP_URL是固定站点来源，
不使用请求Host或用户输入构造链接。生产只允许TLS或STARTTLS，校验证书；明文仅允许明确的本机收件器。
密码仅本机环境注入，不提交Git。SMTP失败不降级、不输出服务器异常，仅脱敏失败计数日志。

请求端点对已知/未知邮箱返回相同202，在响应后处理邮件，避免SMTP延迟成为账户存在性信号。
当前进程后台任务不是持久化投递：进程崩溃或SMTP失败可能丢失邮件，用户须重新申请；不会自动重复发送。
Redis按IP/邮箱限流，后端不信任转发IP；生产BFF/网关限流仍须实际配置。

已实测本机TCP SMTP收信、拒绝注入、TLS不降级、超时，以及真实PostgreSQL/API单次消费/过期/
重发作废/并发/会话撤销。生产外发SMTP、DNS/SPF/DKIM/DMARC、邮箱送达和滥用防护未验收，
所以生产公开注册继续保持fail-closed。临时收件器仅验收协议传输，不代表真实邮箱送达。
