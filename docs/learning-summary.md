# 个人学习进度总览

账户页`/account#learning`手动刷新，从已授权空间读取自己的状态计数。分母是空间内笔记身份数（notes），不是历史修订或来源数量。没有个人学习记录的笔记计入未看，同时单独显示“尚无个人学习记录”；显式未看仍属于已记录。

`GET /api/v1/spaces/{tenant_id}/learning/summary`无查询参数，返回带时区`as_of`、total_notes/recorded_notes/unrecorded_notes、states四种状态和reminders到期/未来数量。提醒以服务器单一时间点划分 `<=`/`>`，排除细看和空提醒。总览不受列表100条上限限制，无学习掌握率或估算百分比。

## 边界与维护

- 复用0020_learning和身份/成员/RLS；一个SQL聚合，左联查显式绑定租户/笔记/当前用户，不读其他成员状态或笔记正文。无新schema、依赖、后台任务或写审计。
- 精确GET BFF和API拒绝额外参数；响应模型校验计数和不变量。Web独立契约验证非负安全整数、时区和计数一致性；切换空间/身份卸载取消，失败清空旧统计，可手动重试。无定时轮询/通知。
- 新修订不重置已有整篇阅读状态。不是按修订的复习完成情况或学习计划。统计时间后的状态变更需再次手动刷新。
- 修改口径须同时更新`modules/learning/summary.py`、Web `lib/learning-summary.ts`、Python/Node契约测试和真实集成/浏览器用例；不将未保存视为真实写入的学习记录。
- 统一检查见`docs/maintenance.md`；真实集成`tests/test_learning_summary_integration.py`，契约`tests/test_learning_summary.py`/`scripts/learning-summary.test.mjs`；浏览器`apps/web/tests/knowledge.spec.ts`。

权限或数据库错误时保留错误码和脱敏证据，不以本地旧数据填充统计。模块不产生业务写入，失败恢复为修复后重新部署/刷新；不需要数据库回滚。不自动连接模型、平台或设备。

## 验收证据

602项非集成、15项Node契约、Ruff/格式/mypy与Web类型/lint/生产构建通过。110项真实集成无失败/错误/跳过，依赖重启、资源限制与受限S3通过；新增用例真实创建103篇笔记验证未记录、100个到期提醒、成员独立和版本不重复。
首轮Chrome的计数/保存/断网恢复通过，但整页手机横向溢出断言失败，不能记整轮通过；账户控件宽度和按钮换行修复。修复及最终19卡片构建均Chrome30/30通过，最终日志`.cache/browser-learning-summary-final-20261108.log`，桌面/手机总览和成果卡安全截图见PROGRESS顶部。不以协议夹具当平台采集、模型推理或用户真实学习成果。
