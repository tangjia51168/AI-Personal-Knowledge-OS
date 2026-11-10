# 个人复习计划（按日分配）

入口 `/account#knowledge` → 跨页「选择提醒」1–20篇 → 读取批量提醒预览 →「按选择顺序分配复习日期」。填写本机时区开始时间、每日1–20篇、间隔1–30个日历天。按勾选顺序分组，跨度最多90个日历天；逐篇展示目标时间后确认保存。修改参数清除确认，修改选择卸载旧预览，身份/空间切换取消并清空。

计划是当前选择的一次提醒分配，每篇只有一个提醒，不保存命名计划或重复排课规则。它覆盖已有提醒，保留状态/完成时间；细看记录仍排除待复习日程。日程/ICS继续消费同一记录，保存后手动刷新。不是间隔复习算法、掌握率、学习历史、后台自动计划或通知发送。

## 契约与授权

- 预览复用 `POST .../learning/reminders/preview`，按选择顺序返回最新标题、个人状态/原提醒和expected。现有统一设置/清除接口保持兼容。
- `PUT .../learning/reminders/plan`：`{items:[{note_id,expected,reminder_at}]}`；1–20个唯一笔记，每项时间必填、非null且带时区，标准化UTC。额外字段拒绝；服务端最大时间差91个连续天，容纳最多90个本地日历天跨夏令时的偏移。接口接受逐篇明确时间，不接收每日篇数/本机时区等规则，也不声称验证浏览器分组规则。
- 同源BFF仅精确PUT、无查询参数、4KiB请求；20项合法请求低于限额。前端纯函数只产生明确UTC时间，不访问平台、模型或设备。
- 身份/有效成员、事务租户上下文、FORCE RLS、显式tenant/user/note，复用authorized_transaction租户写锁及锁后身份复验。expected不是授权凭证；任何个人学习记录变化409 REMINDER_BATCH_CONFLICT，混入无权限/不存在笔记404 REMINDER_NOTE_NOT_FOUND，整批不写。
- 与统一批量/单篇写入使用同一租户锁；整批校验后写不同时间，每个实际修改和learning.updated审计同事务，审计失败全部回滚。no-op不更新或写审计。无新schema/依赖/队列。
- 断网或响应解析失败可能已提交；清除预览/确认，重新预览核对，不自动重试，没有批次幂等回执或整体撤销。纠错可重新分配或用既有批量清除。

## 本机时间语义

`reviewPlan`使用浏览器本机日历加天，跨夏令时保持小时/分钟；起始日期无效或遇到不存在的本地时刻时拒绝生成。秋季重复时刻按浏览器Date默认较早时刻。当前时区改变后重新读取/生成；服务端保存UTC时间，不保存时区或周期规则，不保证旅行后仍显示原墙上时刻。允许安排过去时间，保存后可立即成为到期记录，页面不宣称所有安排都在未来。

## 维护与检查

- 时间分配：`apps/web/src/lib/review-plan.ts`，`scripts/review-plan.test.mjs`覆盖分组/顺序、月年边界、90天上限、输入与夏令时缺失/重复。
- UI复用 `apps/web/src/app/account/reminder-batch.tsx`，无重复选择状态或独立请求层；改参数取消确认、断网清预览、成功清旧清单，保留详情/草稿。
- 后端 `apps/api/app/modules/learning/reminders.py` 的ReminderPlan/apply_plan，共用_apply_reminders，routes.py及精确BFF同步维护；0020迁移不修改。
- `tests/test_review_plan.py`为契约；`test_reminder_batch_integration.py`增加真实HTTP不同时间/日程一致性、no-op、回滚/外租户、与批量竞争、撤权/注销。旧批量回归保留。
- `apps/web/tests/learning.spec.ts`代理边界，`content-filters.spec.ts`真实跨页计划/修改参数/上限/断网/写回/桌面手机与截图。截图只用协议夹具，无平台/模型请求。
- 统一入口`./scripts/check.ps1`，真实独立栈`./scripts/acceptance.ps1 -Hardened -RestartCheck`；浏览器`scripts/account_e2e.py`仅创建清理自身夹具。依赖重启不与浏览器并行。其余见[维护约定](maintenance.md)。

619非集成/20Node、121真实集成/重启/资源/受限S3、Chrome30/30通过；验收后加入第23项成果。无schema变化，本轮不重新声称恢复；新镜像未复扫，生产门禁及既有暂缓未完成。最终样式/构建复核和成果见PROGRESS。
