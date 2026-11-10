# 个人复习分析维护说明

## 契约

`GET /api/v1/spaces/{tenant_id}/learning/analysis` 只读取当前已认证成员在当前空间的 `notes` 与 `note_learning` 聚合。BFF 只允许同源 `GET`，拒绝查询参数；响应使用服务器 UTC 的 `as_of`。

响应包含笔记总数、已保存/未记录数、四种状态、已完成记录数、`completed_at` 近 7 天和近 30 天窗口，以及到期/未来提醒数。所有计数按当前成员显式绑定；事务内保留租户上下文、RLS 和锁后身份复验。

## 时间和限制

`completed_at` 是一条学习记录最近一次保存为非 `unseen` 状态的时间，不是事件表。因此页面不得称其为复习次数，也不得推导连续学习天数、趋势或掌握率。近 7/30 天使用服务器 UTC 的 `as_of - 7/30 days` 边界；状态和提醒仍以当前记录为准。

分析是只读操作，不写审计、不发送通知、不调用模型或平台。加载失败清空旧结果并允许手动重试，空间或身份切换通过组件卸载取消旧请求。真实空空间显示零值，不创建演示数据。

## 验收和维护

- `tests/test_learning_analysis.py`：Pydantic 不变量、单聚合 SQL、租户/成员绑定。
- `apps/web/tests/learning.spec.ts`：BFF 方法、查询和路径边界。
- `scripts/learning-analysis.test.mjs`：前端响应解析与反伪造计数。
- 固定入口：`./scripts/check.ps1`；独立栈使用 `./scripts/acceptance.ps1 -Hardened -RestartCheck`，随后运行账户浏览器验收、`scripts/operations_check.py --acceptance` 与 `scripts/tls_gateway.py --acceptance`。

本模块不改变迁移；恢复沿用既有 `0020_learning` 证据。真实集成、浏览器、生产构建和外部客户端并不因本地契约测试自动视为完成。
