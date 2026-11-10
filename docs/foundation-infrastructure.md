# 架构与基础设施维护与验收

本机跨实例演练、运行观测与锁文件盘点是本主线的独立维护消费者。它们不替代真实异地、生产容量、外部告警或独立安全评审。最新实际结果见PROGRESS；未执行的命令不能记通过。

最终恢复证据`.cache/foundation-recovery/8c4ee2b9d9dd42ecb353f91070d15151/summary.json`：15项检查全通过，33表/2对象、快照10.156秒、恢复业务13.578秒、冷队列22.109秒；源暂停与演练58.672秒，全部源服务健康、精确自身夹具清理。24次并发4读取零失败，P50为31毫秒/P95为47毫秒/最大63毫秒。最终固定检查675非集成/31Node与148真实集成/重启/受限S3通过；不计成全业务容量或生产RPO/RTO。

## 本机独立恢复

```powershell
.venv/Scripts/python.exe scripts/foundation_recovery.py --acceptance
.venv/Scripts/python.exe scripts/operations_check.py --acceptance
.venv/Scripts/python.exe scripts/foundation_audit.py --inventory
```

第一个命令短暂停止现有隔离验收项目写入，使用真实注册/文件上传/待执行任务建立仅自身的随机协议账户；调用既有AES-GCM快照归档，不复用旧schema证据。在相同宿主新建独立PostgreSQL、Redis、MinIO及API/Worker/Beat，所有镜像固定为源容器的Image ID，禁止自动拉取/模型/平台/SMTP调用。固定源标签和15432/16379/19000/18000回环端口校验失败则停止。

恢复重建非superuser/nonbypass应用角色，比较全库行、表列ACL、RLS/FORCE RLS、策略、复合关系/约束、触发器/函数；验证每个对象SHA-256、匿名拒绝、STS策略限制。API使用恢复会话读取文件并重新签发下载票据；匿名/另一租户拒绝。全新空Redis不复制源状态，由独立Beat从DB outbox重新发布任务，独立Worker读取恢复数据并保存结果。源服务恢复后再次验证原数据，复用既有精确夹具清理，仅清自身账户/对象，不删除其他数据或共享限流桶。

每轮生成独立随机容器/网络/卷；完成或失败均停止自己创建的容器，保留卷/加密归档/失败现场，不执行down -v、drop、truncate或删除数据卷。检查`.cache/foundation-recovery/<run>/summary.json`全部checks及status，失败不得只挑部分成功记完整通过。Docker Desktop内部bridge无法发布回环端口，数据服务/API另接operator bridge，Worker/Beat仅连internal bridge。API平台/模型/邮件关闭；此配置不是生产网络策略。

耗时使用单调时钟：capture_seconds为停写后快照/加密完成时间，business_ready_seconds从归档认证完成前的捕获结束点到恢复API业务读取完成，outbox_ready_seconds再包括独立Worker结果核验。它们是该次少量协议数据实测，非生产RTO承诺；停写快照期间不接受新写入，不推导生产RPO。原Redis AOF损坏卷及修复副本按既有记录保留，本演练不证明Redis历史临时状态无损。

每轮恢复24个并发度4的授权资产列表请求，排除一次预热，记录P50/P95/最大值和吞吐；失败响应直接失败，不通过重试掩盖。不代表全业务或极限容量，不能将同一宿主竞争下的样本用作生产SLA。

恢复签名/凭证加密配置仅从已运行验收API在内存传给新运行容器；报告不保存秘密，Docker管理员仍能查看容器环境。测试STＳ凭据一小时到期，新目标保留停止状态，不承诺直接长期运行。Windows DPAPI归档密钥仅当前用户可解；异地使用须先完成独立密钥托管和密钥迁移，不能直接把DPAPI文件复制到另一台机器就宣称可恢复。源签名密钥丢失会使旧票据失效；凭证加密主密钥丢失会使模型凭证不可解，生产应强制注销恢复会话并核对外部副作用。

## 观测与检查

全库指纹验证后，仅在恢复目标标记其他outbox已隔离；独立Worker只执行本次随机协议任务。第二条真实queued协议任务保持queued/attempt_count=0验证隔离有效，原栈任务不修改。API指标恢复后的消费者也在正式演练检查中；所有运行源服务回切后逐个健康核验。

API的操作员接口`GET /api/v1/system/metrics`只允许SYSTEM_API_TOKEN身份，经现有HTTP边界禁止缓存。仅按框架路由模板/固定HTTP方法记录请求数、4xx/5xx、耗时sum/max及累计直方图；不收集用户/租户/原始URL/查询/请求正文/凭证。线程安全、最多256序列，超限有明确计数。进程重启清零，不跨进程汇总，也不是时间窗口告警。

operations_check是实际读取消费者，报告保留脱敏快照并检查overflow；同时继续检查DB角色/outbox积压、Worker、Redis、S3、容器资源。网关继续阻断system路径。当前无外部导出器、OpenTelemetry SDK分布式链路或Sentry告警；不向第三方发送请求/异常内容。接入时须重新核对采样、数据字段、保留和独立目的地。

## 可复现供应链

foundation_audit离线检查Python三份锁的精确版本/sha256和一致性，以及Node lock v3每包version/integrity，报告输入锁SHA-256。Python许可来自相同版本本机distribution元数据，未安装/版本不符记缺失；Node来自锁文件license。此清单不是许可审查或CVE扫描，缺元数据不能猜许可。MinIO为AGPL-3.0，Redis许可依具体镜像版本核查；商业分发/托管前需审查真实镜像SBOM、NOTICE、源码义务及签名。

固定回归：`./scripts/check.ps1`；真实全栈：`./scripts/acceptance.ps1 -Hardened -RestartCheck`。不与恢复/浏览器并行；迁移已追加0025并冻结，既有历史迁移不修改。新增自动回归覆盖目标归属/回环绑定/固定镜像、失败对象哈希、性能失败不可计通过、指标权限/脱敏/并发/内存边界与锁文件损坏。单元测试不是恢复集成通过。

## 如何提供真正异地目标

当前可自动提供同机隔离实例，无法凭空获得另一台物理设备、云账户、预算和访问权。推荐先使用自己已有的第二台设备，通过私人VPN或SSH隧道连接；也可自己创建云测试机，费用和地域由你选择。无需购买资源也可先保留异地未验收。

1. 复制`infrastructure/recovery/target.example.json`到Git忽略的`.cache/recovery-target.json`，填写已有设备/SSH或VPN配置别名、目标独立目录、独立归档位置、独立离线密钥或KMS配置位置；填写能接受的最大数据丢失时间RPO与恢复时间RTO。
2. 密钥保存本机权限受限的配置或系统密钥库，不填写到聊天、不提交Git，不上传平台Cookie。目标应有独立磁盘和备份；本机另一个容器/盘符不是异地。
3. 告诉我“配置文件在某绝对路径，允许在该目标创建新隔离实例”，并说明已有Docker/Compose及访问方式即可。若工具未安装，再按目标系统具体准备，不自动安装系统工具。
4. 获得可用目标后核对空实例与访问权限，先验证独立密钥可解，再传输已认证加密归档；恢复角色/DB/S3和配置、全量一致性、Worker权限/过期租约、冷Redis、真实业务/撤权、断源访问与切回，并记录实际RPO/RTO。失败保留两侧数据；公网域名/TLS、生产切换另行确认。

示例回复只含位置：“异地目标用我已有的备用电脑，配置在<workspace>/.cache/recovery-target.json，允许创建独立恢复环境。”你也可以只提供第二台机器的操作系统和现有访问方式，我再给出对应步骤。
