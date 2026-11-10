# 隔离备份与恢复演练

0024最新同机新库恢复含概念卡片33表/1对象，见PROGRESS历史记录。新增正式`scripts/foundation_recovery.py --acceptance`在全新PG/Redis/MinIO/API/Worker/Beat完成跨实例业务恢复，33表/2对象、会话/文件/租户隔离和空Redis outbox通过，最终报告`8c4ee2b9d9dd42ecb353f91070d15151`15项全通过，包含第二任务隔离与恢复指标消费者；源栈全部服务回切健康与自身夹具清理通过。具体命令、实测耗时/性能与独立密钥/Redis/网络限制见[架构主线](foundation-infrastructure.md)。以下“未切换至API/Worker”仅对应旧backup_recovery同实例入口；新跨实例仍在同一宿主，异地/生产恢复未验收。

2026-11-08：0020学习状态后的最新同机全量持久化备份/恢复通过32表、1对象、RLS/FORCE RLS、权限和表约束、摘要及匿名403：`.cache/recovery-drills/0fa4265350dc481bbe6cc7293258bdda/summary.json`。补验恢复后的学习状态、提醒/完成时间、两份历史修订、学习审计和无上下文应用角色不可读通过；重启后原库记录相同。自身随机协议账户已清理，原数据/命名卷、新目标和加密档案均保留。辅助编排首次被中断后服务已恢复，随后只读验证原目标通过，不重新创建或覆盖目标。

“全量”仅指本机该数据库和私有桶中的持久化数据，不含Redis临时状态、部署配置或外部主密钥。便携的用户空间全量下载/导入、异地副本和生产切换仍未实现/验收。不得把Obsidian批量修订包称为完整备份。

0017人工分类后的同机演练通过29表/数据/RLS/表与列权限/约束和1对象、匿名403：
`.cache/recovery-drills/489667a3a1c7486eba95410ea8b142cd/summary.json`（2026-11-07T11:55:50Z）。
补充检查`.cache/label-recovery/5fa0e2b1441f46e5856640f193eee2cd/summary.json`验证标签名称/规范键、
启停状态与操作者、两份历史修订、应用角色RLS及重启后的原API。协议夹具已清理、原数据/卷与恢复目标保留。
前两次辅助脚本失败记录保留在PROGRESS.md；环境继承覆盖问题已修复并复验原MinIO配置与受限身份。
Compose子进程必须隔离Python临时运行凭据，防止覆盖显式env文件中的bootstrap配置。
production_recovery_verified仍false；以下0016及更早结果为历史证据。

0016人工修订后的同机演练通过27表/数据/RLS/表与列授权/约束和1对象、匿名403：
`.cache/recovery-drills/8f37622838bc44eb8705fa971e5afde8/summary.json`（2026-11-07T10:22:00Z）。
恢复包含明确标为协议内容的人工修订，恢复后只读确认人工origin、空AI run、base与个人理解；
不是新的平台采集或模型推理。源夹具清理、原数据/卷保留，production_recovery_verified仍false。
下文0014及更早演练均为历史证据。

0014后的同机演练已通过27表（含真实公开快照、笔记及未知用量记录）、RLS/表与列授权/
约束/数据和1对象，匿名403；证据`.cache/recovery-drills/60543245e57c4ee39863b10b1fd226b0/summary.json`。
RLS的USING/WITH CHECK与CHECK约束均经PostgreSQL原生重解析，兼容pg_dump等价cast转写；
真实策略变动及扩大列授权仍被真实回归测试检测。此前首次误差现场保留，未删除源数据。
下文0010演练是历史记录；production_recovery_verified仍false。

`scripts/backup_recovery.py --acceptance` 只面向现有 `knowledge-os-acceptance` 本机验收栈。
脚本核验 PostgreSQL/MinIO 的 Compose 容器标签、端口绑定与 `.env.acceptance` 的
15432/19000 回环地址；拒绝连接串中的额外 query/host 覆盖参数。
运行前必须停止该项目的 api、worker、beat、web，避免数据库快照与对象内容在备份期间发生变化。
脚本自行检查这些容器已停止；PostgreSQL、Redis、MinIO 保持运行。

```powershell
docker compose --env-file .env.acceptance --env-file .env.storage-runtime.acceptance -p knowledge-os-acceptance -f docker-compose.yml -f docker-compose.acceptance.yml -f docker-compose.storage-runtime.yml -f docker-compose.hardened.yml -f docker-compose.gateway.yml stop gateway api worker beat web
./.venv/Scripts/python.exe scripts/backup_recovery.py --acceptance
docker compose --env-file .env.acceptance --env-file .env.storage-runtime.acceptance -p knowledge-os-acceptance -f docker-compose.yml -f docker-compose.acceptance.yml -f docker-compose.storage-runtime.yml -f docker-compose.hardened.yml -f docker-compose.gateway.yml up -d --wait
```

演练在专属 `acceptance_checks.recovery_markers` 中新增一行，在原私有 bucket 新增一个随机测试对象，
以证明备份包含真实数据。PostgreSQL 使用导出事务快照与 `pg_dump -Fc`，对象按分页读取。
归档、对象和清单均使用随机 AES-256-GCM 密钥加密；数据库明文归档不会落盘。
归档上限 512MiB，对象总量上限 256MiB/10000 个；超限退出并保留加密现场。

所有归档位于 Git 忽略的 `.cache/recovery-drills/<随机运行 ID>`。
Windows 密钥文件由当前用户的 DPAPI 保护；其他系统创建 0600 密钥文件、0700 目录。
Windows 必须使用实际运行 Docker Desktop 的当前用户；受限沙箱账号可能无可用 DPAPI 配置，
这种情况脚本会失败退出，不降级为明文保存密钥。
这套密钥保存方式用于同机验收演练。真正异地灾难恢复需要管理员另行设置独立离线密钥托管、
副本存储、权限和轮换，当前演练不能证明这些生产条件已完成。

恢复前先完整验证 GCM 认证标签、大小和 SHA-256。随后创建全新随机数据库
`knowledge_os_recovery_<ID>` 和新私有 bucket `knowledge-os-recovery-<ID>`，不覆盖原库或 bucket。
恢复包括行内容、表级 RLS/FORCE RLS、所有者/表授权/列授权、策略、表约束及对象校验；
约束比较PK/FK/UNIQUE/CHECK定义、验证/延迟状态，CHECK用PostgreSQL临时视图原生重解析，
接受pg_dump等价数组cast转写，同时拒绝定义与NOT VALID状态漂移。
流式选项仅作用于行读取语句，不保留在后续DDL连接上。
另实测恢复对象的匿名 HTTP 访问为 403。
成功证据写入该运行目录的 `summary.json`，其中 `production_recovery_verified` 始终为 false。

成功或失败均保留加密归档和新恢复目标，方便核查；原库/原 bucket 只清理本次随机测试标记。
脚本不删除数据库、bucket、表或命名卷。若需清理恢复目标，应先读对应 summary/清单，
核对精确名称，再由管理员明确授权处理，不使用宽泛匹配或 `down -v`。

当前脚本与单元测试只证明加密和输入边界；只有执行演练并检查实际 summary 后才能记录恢复通过。
该脚本在同一 PostgreSQL 实例创建恢复库，复用实例已有角色与扩展，未测试跨实例角色恢复；
不备份 Redis broker/result 临时状态、Compose 配置或外部模型凭证加密主密钥。
持久化业务任务的数据库 outbox 可恢复发布，但恢复库未切换至 API/Worker，
因此不能据此声称业务切换演练或生产 RPO/RTO 已验收。

0010_auth_security_events后的最新演练已校验19张表、RLS/授权/表约束指纹和1个对象，匿名访问拒绝；
目标数据库与私有桶为独立创建，原数据和命名卷保留。证据目录为
`.cache/recovery-drills/093bb6cf62ea423f88050addfdf5d5b4`（2026-11-06T15:29:10.886354+00:00）。
新增真实DB回归覆盖约束定义、验证状态变化及合法等价cast转写。首次约束比对演练因该转写产生差异，
失败加密现场仍保留；修复后重跑成功，不将首次失败记录成通过。
已启用本机网关时，上述命令一并停/启gateway，保持只读/资源加固覆盖；无网关时可省略其覆盖与服务名。
