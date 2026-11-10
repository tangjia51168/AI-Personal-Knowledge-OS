# 开发与验收补充

## 健康状态语义

- /health/live：进程可响应，不等待依赖。
- /health/ready：PostgreSQL 连接、应用角色无 bypass、迁移版本、租户表 RLS，Redis PING，S3 bucket HEAD。
- /api/system（Next.js）：服务端只请求已配置后端，状态过滤后返回浏览器。
- 不含 Worker 存活的推测；Worker 必须通过队列 smoke 或容器健康检查真实验证。
- 现阶段健康端点用于本机工程诊断；生产入口应限制详细诊断端点可见范围。

## 初始化验收清单

本机Docker/WSL已运行。最新加固验收23/23、重启持久化/受限S3、本机TLS与282项非集成检查已通过；
最新报告以PROGRESS.md顶部为准。以下清单用于新环境复现，不表示当前缺少Docker。

1. 准备 .env，运行 scripts/prepare_minio.py 校验并缓存官方 MinIO 文件，再执行 docker compose config / up --build -d / ps。
2. 验证 migrate 与 storage-init 成功退出，其余服务健康/运行。
3. 浏览器打开控制台，4 项依赖为已连接；API /health/ready 为 200。
4. 执行 scripts/smoke_stack.py，验证 Redis 与独立 Worker 实际收发。
5. 专门测试数据库运行真实 RLS 集成测试，无任何 skip。
6. 在测试库写入数据后重启 postgres/minio/redis，检查命名卷保留；禁止删除卷。
7. 验证对象存储拒绝匿名访问；完整租户文件接口后续另验收。

身份/权限、持久化任务、文件授权与DeepSeek真实连接/云端配置回退已验收；Ollama仍需明确授权地址/模型。
当前0009同机独立目标恢复18表/RLS/权限/约束/1对象通过；生产配置和异地切换仍需实测。
完整Phase 0及公开上线门禁见TASKS.md和docs/release-gates.md，不进入Phase 1。

### Windows 重启后仍提示 WSL update required

Windows 内置 WSL 功能与新版 WSL 运行时不是同一件事。启用功能后，若 Docker 日志提示
`WSL update required`，且 `wsl --version` 不受支持，需要安装/更新 Microsoft 官方 WSL 运行时。
本轮已获准启动 scripts/install-wsl-runtime.ps1，管理员操作日志在
.cache/container-prerequisites/install-wsl-runtime.log。本轮 WSL 2.7.14.0 已安装并验证，无需重复安装。
无需安装 Ubuntu 等额外发行版，也不要删除/注销 Docker 发行版或数据卷来解决版本问题。
安装完成后确认 `wsl --version`，启动 Docker Desktop，确认引擎 ready，再运行 acceptance.ps1。

验收脚本将真实测试报告存为 .cache/acceptance-reports/integration.xml；任何 skip/缺项/失败均不通过。
失败时保留测试容器，重新运行使用唯一容器名称，不覆盖先前失败现场；不删除命名卷。

## 依赖安全已知项

首次 2026-11-05 在线 npm audit 报告 8 项 moderate，源于开发依赖
@typescript-eslint/typescript-estree → minimatch 10.2.6，公告 GHSA-g3hv-75wg-m249。
8 项包含上游工具包的传播项，不是 8 个独立根因。
续作已通过定向 overrides + 全新依赖解析修复，实际 parser 加载 minimatch 11.0.1，在线 audit 为 0。
旧锁文件增量安装未应用补丁；旧依赖及 lock 已可恢复移动至 .cache/phase0-dependency-baseline。
scripts/check-dependency-patch.mjs 验证实际 require 路径与基本匹配调用。
未强制降级 Next/eslint-config；跨主版本覆盖当前通过 lint/typecheck/build，仍需持续跟进上游原生修复。
离线 audit 空结果不能作为已修复证据。ESLint 9 有官方停止支持提示，需独立规划兼容升级。
