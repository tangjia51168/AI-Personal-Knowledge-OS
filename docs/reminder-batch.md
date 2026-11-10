# 批量复习提醒安排

入口 `/account#knowledge` → 笔记结果「选择提醒」→「批量复习提醒安排」。逐篇明确选择，最多20篇，跨页保留；读取最新预览后统一设置或清除自己的提醒时间。保留阅读状态和完成时间，没有记录时设置提醒会建立未看记录；清除本来没有的提醒不新建记录。

后续增量[个人复习计划（按日分配）](review-plan.md)在同一预览里提供按选择顺序/每日篇数/间隔天数分配不同提醒时间，使用独立plan PUT并共用原子写入；既有统一设置/清除契约不变。具体增量验收状态以PROGRESS顶部为准。

## 接口与一致性

- `POST .../learning/reminders/preview`：`{note_ids: UUID[]}`，1–20个且不得重复。返回同顺序的最新标题、个人状态/原提醒与expected摘要；一个有界联查，不写业务记录。无其他成员记录。标题只供预览，不作为笔记身份。
- `PUT .../learning/reminders/batch`：`{items:[{note_id,expected}],reminder_at:带时区时间或null}`。reminder_at必须显式提供，null明确清除；UTC标准化。不接受status/user/tenant覆盖，1–20个唯一ID。
- 两者身份/有效成员验证、事务内租户上下文、FORCE RLS，使用已有authorized_transaction租户锁和锁后会话复验。租户/用户/笔记查询显式绑定，不能用header指定用户。BFF精确POST/PUT、无查询参数和4KiB请求限制；正常20项请求小于此限。
- expected是包含个人学习记录ID、状态、时间等字段的SHA256；无记录用固定null摘要。不是授权凭证，仍逐批验证身份/成员/笔记。任一记录与预览不同返回409 REMINDER_BATCH_CONFLICT，混入无权限或不存在笔记404 REMINDER_NOTE_NOT_FOUND，整批不写。
- 所有校验先完成后修改；每个实际变更同事务追加learning.updated审计，审计失败全部回滚。相同提醒为no-op，不改时间/审计；单篇最多一次写入，整体规模20。无新schema/依赖/队列或外部调用。
- 并发写入由已有租户锁串行化；旧预览重放被拒绝，不自动重试。网络失败可能已提交，须重新预览核对再确认；没有持久化批次ID或幂等回执，不把响应丢失称为失败回滚。

## 页面与维护

选择项仅存当前组件内存，切换身份/空间卸载清空并取消；改变选择卸载旧预览，改时间/操作必须重新确认。提交时暂停列表选择/分页；成功清旧列表并提示刷新，总览/日程/已打开笔记状态需手动刷新。详情与草稿保留。读取/写入失败丢弃预览和确认，选择仍保留，提示核对后重试。细看记录可以保留提醒但不会进入待复习日程，页面明确说明。

- API：`apps/api/app/modules/learning/reminders.py`及routes.py，复用service/content/models授权与时间口径；0020历史迁移不改。
- Web：`apps/web/src/app/account/reminder-batch.tsx`、knowledge.tsx，BFF `apps/web/src/app/api/account/[...path]/route.ts`。
- 固定回归：`tests/test_reminder_batch.py`、`test_reminder_batch_integration.py`；Web learning.spec.ts与content-filters.spec.ts验证精确代理、跨页选择、预览确认、真实写/清除、冲突与断网恢复及手机截图。
- 命令见[维护约定](maintenance.md)：统一`./scripts/check.ps1`，独立栈`./scripts/acceptance.ps1 -Hardened -RestartCheck`及`scripts/account_e2e.py`；依赖重启不与浏览器并行。
- 恢复：无需迁移回滚，修复后刷新预览；设置错时间可重新预览并明确清除/改正，无批次撤销功能。未知结果先核对，不能盲重试。全空间便携备份/第三方客户端/生产门禁范围不变。

不是自动学习计划、按间隔算法排课或通知发送。仅手动同一时间批量安排，未实现的学习历史/掌握率/通知/双向同步仍保留未完成。617非集成/17Node、119真实集成/重启/资源/受限S3和Chrome30/30通过；具体日志、最终构建与截图见PROGRESS。看板新增本模块成果，不将整条学习主线标为完成。
