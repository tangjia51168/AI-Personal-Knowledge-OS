# 公开来源定时计划维护

入口：账户任务中心“公开来源定时同步”。定期刷新已保存公开来源，不连接收藏夹账号、上传Cookie、调用模型或写分类建议。外网仍经integrations/platforms SSRF/DNS/重定向/TLS/大小/总时限边界。

## 调度与数据

0027追加source_schedules/source_schedule_ticks/source_schedule_jobs。计划属于有效成员，每人最多20个，同一人/来源唯一；间隔360至10080分钟。GET只读自己计划；POST同来源同间隔回放，PATCH必须expected_version，暂停无需再授权，恢复必须确认。采集关闭也可保存，到期明确报COLLECTOR_DISABLED，不访问网络。

Beat每30秒发布不透明UUID发现记录。ticks仅UUID/时间/actor索引，不是授权；Worker复验实际用户/租户/成员/计划，租户锁后复验，以计划版本/到期派生幂等键，复用source_collect/配额/outbox。暂停取消计划未完成任务，采集网络前后再检查，暂停后不提交。已开始HTTP不能撤回；恢复从新周期起，不补跑错过周期。撤权停止计划。

计划/任务用tenant/创建成员/来源复合FK，FORCE RLS保护；全局发现表最小列权限。实际计划变更与审计原子提交，不可变schedule_jobs绑定真实创建者、来源及source_collect任务。

刷新只与同来源最新快照比较：provenance、响应哈希、正文、类型、标题/作者、平台、最终地址、metadata和limitations都相同才复用并返回unchanged。人工导入不能替代平台快照；A→B→A保存三个版本，元数据变化留档。失败/配额不报成功。未变化仍消耗一次普通采集任务配额，不调用模型。

## 维护与局限

UI读取候选/计划、确认保存/暂停/恢复；冲突或网络未知重新读取，不盲目重试。候选为当前最近20条已接受来源，截断明确提示；不发现收藏夹新内容，也不是完整增量索引。日/周对应分钟、自定义最短6小时；无任意cron/时区/邮件送达。

固定回归：check含输入/Node契约；track_acceptance.py --track sync --run integration或browser；acceptance.ps1含真实Broker/Worker禁用路径、并发/暂停/撤权/RLS。快照变化回归在collection真实DB集合，标明协议连接器结果，不记公网采集成功。本轮无新平台外部实测；恢复读取暂停计划并核对版本/来源，防止演练意外访问。
