# 分类目录维护

账户的知识笔记区提供“分类目录维护”。空间成员可以读取包含启用和归档状态的目录，对人工主题或标签重命名，也可以归档或恢复分类。

维护接口是 `GET /api/v1/spaces/{tenant_id}/labels/manage` 和 `PATCH /api/v1/spaces/{tenant_id}/labels/{label_id}`。更新请求必须携带 `expected_version`，并且至少包含 `name`、`active` 或主题的 `parent_id`。省略parent保留、null移根及层级边界见[主题层级](topic-hierarchy.md)。名称仍使用既有 1–60 字符、240 字节 UTF-8、控制字符拒绝和同类型规范化唯一约束。版本不匹配返回 `LABEL_VERSION_CONFLICT`；同类型重名返回 `LABEL_NAME_CONFLICT`。

归档禁止新关联，已有笔记关联和分类筛选保留；恢复后可以继续关联。归档名称仍占用唯一名称与100个目录配额。重复创建归档名称返回 `LABEL_ARCHIVED`，不会隐式恢复。旧的笔记分类接口保持原响应形状；前端从管理目录读取状态，只把启用分类作为新关联选项。

数据迁移 `0021_label_management` 追加 `active`、`version`、`updated_at`；`0022_label_update_actor` 追加更新者复合外键与当前成员绑定的 RLS 检查。0021首次在隔离库应用后，补充变更使用0022推进，不修改已应用DDL。只授予指定列更新权限，归属/类型/创建者/删除仍不可改。每次真实目录变更和审计在同一授权事务提交；无变化不递增版本、不写审计，旧版本始终409。租户锁后重新验证会话和成员。BFF精确GET/PATCH、无查询参数、16KiB请求体；无模型或平台请求。

前端明确确认共享分类后保存，改参数取消确认；保存成功清旧目录与笔记清单，重新读取关联。读取/保存失败清旧目录并要求手动核对；身份/空间切换卸载取消。网络未知不自动重试。名称与状态用于当前整篇笔记，历史修订显示的也是目录当前名称；审计记录动作/操作者/资源，不记录改名前后名称，不能当作目录修订历史。

维护检查包括 Python 契约测试、BFF 方法/路径边界、真实数据库的并发版本冲突、成员/租户隔离、撤权和审计回滚，以及账户页面的重命名、归档、恢复和手机布局回归。该模块不引入语义分类、层级图谱、模型调用、通知或自动建议。

## 复验与当前证据

固定检查为 `./scripts/check.ps1`；隔离服务集成为 `./scripts/acceptance.ps1 -Hardened -RestartCheck`。浏览器在已有隔离测试环境下运行 `.venv/Scripts/python.exe scripts/account_e2e.py --test-file note-labels.spec.ts --test-file project-progress.spec.ts`，创建并清理自身协议账户；不调用平台或模型。环境准备约束见[维护约定](maintenance.md)。

2026-11-08最终624非集成、21项Node契约与静态/生产构建通过（`.cache/check-label-management-delivery.log`）；128项真实集成0失败/错误/跳过，含重启、加固资源和受限S3（`.cache/acceptance-label-management-verified.log`）。最终Web专项Chrome4/4通过（`.cache/browser-label-management-delivery.log`），桌面/手机及第26项成果截图已目视检查。此前全套Chrome29/30，收件箱触发共享登录IP限流；未放宽产品限流，不宣称全套通过。

新0022库的32表/1对象加密恢复通过，验证数据、约束、RLS/列权限与对象摘要，源数据保留（`.cache/recovery-drills/6ea71545c76d442cbc0e4b1565b87043/summary.json`）。最终本机TLS与只读运维通过；详细报告见PROGRESS顶部。恢复仅同机隔离演练，不是便携完整空间包或生产异地灾备，新镜像未复扫。
