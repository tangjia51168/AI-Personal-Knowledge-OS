# 个人复习日程与日历导出

入口 `/account#learning` →「个人复习日程与日历导出」。复用已保存的个人提醒，查看已到期或未来7/30/90天的待复习笔记，按提醒时间升序排列，并可下载标准ICS文件。不是自动学习计划或间隔复习算法。

后续[个人复习工作台](review-workbench.md)增加条目「开始复习」入口，复用笔记/证据阅读并确认本次状态和下次提醒。工作台打开期间暂停日程刷新/下载，范围切换取消并清空；保存后清旧日程，手动刷新核对。具体增量验收以PROGRESS顶部为准。

## 契约与时间口径

- `GET /api/v1/spaces/{tenant_id}/learning/schedule?period=due|7|30|90`，默认7。参数模型禁止额外字段；BFF拒绝未知/重复参数、非法范围与非GET。
- 身份与有效成员验证后，重新检查会话；现有租户上下文/FORCE RLS及显式租户/用户条件保护。一个有界联查读取当前用户记录与最新笔记标题，不按历史版本重复，无业务写入。
- 单次请求固定UTC `as_of`。due为提醒时间 `<= as_of`；未来为 `(as_of, as_of + N×24小时]`，不按本地自然日切片。排除细看、空提醒及没有已保存修订的笔记。排序为提醒时间、笔记UUID升序。
- 返回 `as_of/until/period/limit/truncated/items`；条目为笔记ID、最新标题、状态、提醒/记录更新时间。最多100条，多取1条识别截断；不是全部记录统计或冻结快照。超限需缩小范围或先处理逾期记录。

## ICS与资源限制

`GET .../learning/calendar`使用相同参数和授权，下载时重新读取，不保证与刚显示的列表完全一致。截断时返回409 `LEARNING_CALENDAR_TRUNCATED`，不静默输出部分日历；总包最多256KiB，超限413。无事件的接口可输出合法空日历，页面空态禁用下载。

RFC5545：UTF-8、CRLF、每物理行最多75字节且不拆多字节字符、TEXT转义。DTSTART/DTSTAMP/LAST-MODIFIED均UTC；事件无时长，TRANSP透明。稳定UID绑定租户/用户/笔记的SHA256，不输出成员ID、凭证、正文、账号邮件、URL、附件、参与者或VALARM。标题纯文本保留，输入换行不能新增属性。固定文件名、attachment/no-store/nosniff/sandbox响应。

文件可由用户手动选择导入日历。不会连接日历账号、安装/启动客户端、写用户设备或自动通知；重复导入、客户端提醒默认值和时区呈现取决于客户端，第三方客户端未实测。状态变化不会更新/取消已导入事件；不是订阅地址或双向同步。

## 维护入口

- API查询与契约：`apps/api/app/modules/learning/schedule.py`；序列化：`calendar_export.py`；路由：`routes.py`。不修改0020迁移，无新schema/依赖。
- BFF：`apps/web/src/app/api/account/[...path]/route.ts`精确路径、方法、范围白名单及下载头。
- 页面：`apps/web/src/app/account/learning-schedule.tsx`；响应校验：`apps/web/src/lib/learning-schedule.ts`。换身份/空间/范围卸载并取消旧请求，清空旧日程；失败清空/手动重试，不定时轮询。改学习状态后手动刷新。
- 测试：`tests/test_learning_schedule.py`（转义/字节折行/UTC/UID/上限）、`test_learning_schedule_integration.py`（真实DB/RLS/排序/只读/101条截断/撤权注销）、`scripts/learning-schedule.test.mjs`（错误响应拒绝）、Web `knowledge.spec.ts`与`learning.spec.ts`（真实下载/断网/手机及代理）。均进入固定检查。
- 命令见[维护约定](maintenance.md)：统一 `./scripts/check.ps1`，独立栈 `./scripts/acceptance.ps1 -Hardened -RestartCheck`；浏览器 `scripts/account_e2e.py`。集成与浏览器不同时重启依赖。
- 恢复：无新持久化字段；修复后重新构建并手动刷新/下载，不执行破坏性迁移。日历文件导入后由用户在客户端管理；服务不能撤回旧文件。

## 验收与成果

615非集成、17Node及类型/lint/生产构建通过；116项真实集成（0失败/错误/跳过）、重启持久化/资源/受限S3和Chrome30项通过。首轮上限测试准备数据遗漏旧表必填字段，修正后完整复验；没有放宽产品约束或改变历史迁移。

成果入口 `/progress#module-learning-schedule`，截图位于 `apps/web/.cache/module-results/learning-schedule-protocol.png`、`learning-schedule-mobile-protocol.png`，成果卡片为 `learning-schedule-delivery-card.png`。截图仅自身隔离协议账户，不代表真实平台采集或学习效果。实际最终构建/运维/TLS证据按PROGRESS顶部记录。无schema变更未重新恢复，历史暂缓、生产门禁与未复扫镜像范围保留。
