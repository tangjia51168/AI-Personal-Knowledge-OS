# 本机运行检查

新增`GET /api/v1/system/metrics`仅操作员令牌可读，operations_check将本地有界请求/4xx/5xx/耗时直方图快照写入脱敏运维报告，并拒绝指标溢出。计数从API进程启动开始，不是窗口告警；多进程不汇总。真实API集成已校验权限/计数/查询脱敏，TLS网关阻断该路径。完整契约、恢复/性能及异地资源接入见[foundation-infrastructure.md](foundation-infrastructure.md)。

`scripts/operations_check.py` 是一次性的只读检查，限定于独立的
`knowledge-os-acceptance` Compose 项目。它检查 API readiness、独立 Worker、数据库运行角色与
outbox 积压、Redis 内存策略、私有 MinIO bucket、容器健康状态和资源快照。
存储HEAD使用`.env.storage-runtime.acceptance`中已就绪的受限身份，配置缺失或pending即失败，
不回退root；配置解析及错误输出不显示凭据。实际容器身份/读写拒绝另由storage_runtime_check.py验收。

```powershell
./.venv/Scripts/python.exe scripts/operations_check.py --acceptance
```

检查只使用 `127.0.0.1:18000/16379/19000/15432` 和固定 Compose 文件；它拒绝其他端点，
读取本机配置但不打印密钥，不跟随 HTTP 重定向，不发送外部通知，也不重启/修改服务。
结果写到 `.cache/operations/<run-id>/summary.json`，内容含通过/失败、固定错误类型、聚合计数和7服务CPU/内存百分比；快照不代表容量验收。
任何一项失败都以非零退出。outbox 待发布超过 100 条或最早积压超过 5 分钟会失败；
这不是容量压测、异地监控或公网告警系统。

启用本机gateway后，本脚本仍检查7个核心服务；网关应另运行scripts/tls_gateway.py --acceptance，
该脚本验证实际证书与入口策略。两份报告都通过才记录当前本机HTTPS验收完整。

生产部署仍需把同等检查接入受控的监控和告警渠道，并补齐域名、TLS、邮件验证、异地恢复、
最小权限账户、镜像扫描和独立安全评审。
