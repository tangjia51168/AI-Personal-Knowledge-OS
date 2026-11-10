# 个人学习筛选与复习清单

入口 `/account#knowledge` →「个人学习筛选与复习清单」。按自己的四种阅读状态、尚无学习记录、已到期或未来提醒筛选，支持与关键词、来源、最新版本来源和标签组合；每页20条，可连续翻页、打开笔记和更新学习状态。

## 接口与口径

扩展已有 `GET /api/v1/spaces/{tenant_id}/notes`：`reading_status=unseen|seen|skimmed|deep_read|unrecorded`、`review=due|upcoming`，两者可选且取交集。无记录按未看，同时 `learning_recorded=false`；显式未看仍已保存。到期为单次请求服务器时间 `<=`，未来为 `>`，均排除细看及空提醒。列表增加 `reading_status`、`learning_recorded`、`reminder_at`，只返回当前用户的数据。

按笔记创建时间与UUID倒序分页，先匹配最新修订再应用筛选；不按提醒时间排序。游标是符合当前条件的笔记，游标状态变化导致其退出筛选时返回 `NOTE_CURSOR_NOT_FOUND`，提示回首页。翻页不是冻结快照，期间新增/状态/时间变化可能改变结果。无记录与到期的交集为空，页面显示真实空态。

复用0020、成员授权、租户上下文和RLS；左联查显式绑定租户/笔记/当前用户，一页一条有界SQL，带游标至多两条，无逐行查询或业务写入。复习仅消费已有提醒，学习计划、间隔重复算法、复习历史/掌握率与邮件/推送仍未实现。无schema/依赖/平台/模型调用。

## 维护与恢复

- 查询：`apps/api/app/modules/notes/listing.py`；BFF只转发白名单，拒绝未知/重复参数与非法学习枚举；API禁止额外字段，调用者不能选择用户。
- 消费者：`apps/web/src/app/account/knowledge.tsx`、`learning.tsx`，复用 `content-list.tsx` 和阅读状态词表。表单未应用时不影响分页；成功应用回首页。切换身份/空间卸载取消列表读取，读取失败清空清单并可手动重试；已打开笔记/草稿继续保留。
- 保存学习状态后清除旧清单并提示手动刷新，避免成功保存却仍将笔记列为待复习；不隐藏或丢弃打开的笔记。
- 修改口径同时维护查询/BFF/页面与真实回归：`tests/test_learning_filters_integration.py`、`test_content_list_http.py`、`apps/web/tests/content-filters.spec.ts` 和 `learning.spec.ts`。
- 检查入口见 [维护约定](maintenance.md)。无迁移，恢复方式为修复/重新部署后手动刷新；现有学习写入接口与审计保持原事务语义。

## 验收与展示

603项非集成、15项Node、113项真实集成（0失败/错误/跳过）、依赖重启/资源限制/受限S3及Chrome30项通过；统一检查包括Ruff、格式、mypy、TypeScript、ESLint与生产构建。真实数据库覆盖个人状态隔离、成员撤权/注销、只读查询、组合条件和游标失效；浏览器覆盖分页、断网清旧/重试及保存后刷新。

成果卡片入口 `/progress#module-learning-review`；页面截图位于 `apps/web/.cache/module-results/learning-review-protocol.png`、`learning-review-mobile-protocol.png`，成果卡片为 `learning-review-delivery-card.png`。截图使用自身隔离协议账户，不代表外部采集、模型推理或学习效果。最终构建和本机运维/TLS证据见PROGRESS最新记录。
