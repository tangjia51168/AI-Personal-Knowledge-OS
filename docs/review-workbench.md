# 个人复习工作台

账户学习区 → 刷新个人复习日程 → 对一篇笔记「开始复习」→ 读取笔记与最新个人记录 → 阅读结构化内容/原文证据或原始Markdown → 确认状态和下一次提醒 → 保存本次复习。仅手动读取和保存，不调用模型/平台、不连接日历或设备。

## 行为与契约

- 复用已有授权笔记详情与单项批量预览。详情是读取时最新修订，支持既有结构化阅读/证据定位；个人状态关联整篇笔记，不绑定修订，新修订不自动使确认失效。标题预览与修订可能因并发编辑不同，不声称读取是跨请求冻结快照。
- `PUT .../notes/{note_id}/review`：显式`{status,reminder_at,expected}`。status只允许seen/skimmed/deep_read，reminder_at必须提供、带时区时间或null，expected为最新个人学习快照SHA256。拒绝额外用户/租户字段。
- 身份/有效成员/事务租户上下文/FORCE RLS/显式tenant-user-note；复用现有update与authorized_transaction租户锁，锁后身份复验。任一个人学习记录变化409 REVIEW_CONFLICT，不覆盖新记录；笔记不存在/跨租户404，撤权/注销拒绝。
- 与普通学习状态/批量/计划共享锁。成功更新最后完成时间（服务器UTC）和状态/提醒，同事务learning.updated审计；审计失败回滚。保存后snapshot变化，旧请求重放409。本次复习是明确动作，重复读取后再次保存会更新最后完成时间，不提供复习次数或历史。
- BFF精确PUT、无查询参数、4KiB请求。旧learning PUT保持兼容，仍可直接调整状态；expected仅用于工作台提交，不是认证凭据。
- 工作台打开时暂停日程刷新/下载及另开笔记；关闭后恢复。切换身份/空间/范围卸载取消，不保存草稿到浏览器。修改状态/下次时间清除确认，重新读取丢弃旧内容。保存成功关闭工作台、清旧日程，其他总览/清单需手动刷新。
- 下次提醒留空明确清除原提醒，填写本机时间则转换UTC；允许过去提醒。细看仍不列入日程。无邮件/推送、自动间隔算法或学习历史；完成不表示掌握。
- 断网/异常可能已经提交，丢弃旧内容与确认，重新读取核对后再提交，不自动重试，无幂等回执/撤销。已保存误操作用原有学习状态或提醒编辑纠正，原完成时间无历史撤销。

## 维护

后端：learning/content.py的ReviewUpdate、service.py的可选expected校验和routes.py；快照复用reminders.snapshot，业务只在modules，不改main或0020。
前端：account/review-workbench.tsx、learning-schedule.tsx与精确BFF；复用NoteReader，保留纯文本/无主动HTML资源。
回归：tests/test_review_workbench.py，test_reminder_batch_integration.py的真实完成/日程/旧预览/并发/审计回滚/撤权/注销；Web learning.spec.ts代理边界、knowledge.spec.ts真实阅读/断网/确认/保存/清日程/桌面手机。

统一`./scripts/check.ps1`，独立真实`./scripts/acceptance.ps1 -Hardened -RestartCheck`；浏览器`scripts/account_e2e.py`只创建清理自身协议账户，重启不与浏览器并行。其他命令见[维护约定](maintenance.md)。621非集成/20Node、123真实集成/重启/资源/S3、Chrome30/30与静态/构建通过，验收后加入第24项成果。无schema/依赖/外部调用，不重跑恢复；新镜像未复扫，生产和历史暂缓不放宽。最终构建/卡片与截图见PROGRESS。
