# AI Personal Knowledge OS

可自托管的证据型个人知识管理系统，使用 Next.js/TypeScript、FastAPI/SQLAlchemy、
PostgreSQL/pgvector、Celery/Redis 与私有 S3。当前是持续开发中的预览版本。

支持来源批次与授权文本/字幕导入、网页快照、分段证据笔记、人工版本/批注、主题与概念、
共享收藏/合集、本地语义检索、基础引用问答、学习提醒和多格式导出。
单次登录会话12小时，全部组件共用，权限由后端成员/RLS复验。

本仓库公开源码，不提供托管在线服务。真实平台访问、模型语义质量、外部邮箱、
真正异地恢复、客户端兼容性和生产安全有未验收部分；当前镜像漏洞门禁未通过。
不要将本机/协议测试通过理解为生产发布通过。完整差距见 [规格映射](docs/spec-alignment.md)。

## 本机启动

需要自行准备 Python 3.12、Node.js >=22、Docker Engine/Desktop 与 Compose >=2.24.4。
现有依赖锁覆盖 Windows amd64 / Linux glibc amd64；不自动安装系统工具。
以下命令在 Windows PowerShell 项目根目录执行：

```powershell
python -m venv .venv
./.venv/Scripts/python.exe -m pip install --require-hashes --only-binary=:all: `
  -r requirements/build.lock
./.venv/Scripts/python.exe -m pip install --require-hashes --only-binary=:all: `
  -r requirements/dev.lock
./.venv/Scripts/python.exe -m pip install --no-deps --no-build-isolation -e .
npm ci
Copy-Item .env.example .env
# 编辑.env，替换所有CHANGE_ME；数据库URL密码必须与相应角色一致。
./.venv/Scripts/python.exe scripts/prepare_minio.py
docker compose up --build -d
```

开发入口为 http://localhost:3000/account ，项目成果为 /progress，API为 localhost:8000/docs。
所有端口仅限本机。持久化使用命名卷，备份与恢复流程见 docs/backup-recovery.md。
不要执行 down -v；已有数据库改密码需显式轮换，不是只改.env。
模型调用与公开采集默认关闭；须配置自己的凭据并明确开启，模型/二进制/秘密不随源码分发。

## 检查与维护

```powershell
./scripts/check.ps1
./scripts/acceptance.ps1 -Hardened -RestartCheck -Embedding
```

第二条只操作独立验收项目/测试库，不在生产库运行。外部服务缺失记未验收。
文档中的历史本机报告路径用于说明验收范围，报告和用户数据不包含在此仓库。
生产部署按 docs/deployable-release.md 分离运行与迁移秘密，并先完成 docs/release-gates.md。
维护边界见 docs/maintenance.md，数据库迁移冻结，业务模块与外部集成分层。

## 许可证

原创项目源码按 Apache-2.0，见 LICENSE 和 NOTICE。第三方依赖、模型及独立运行服务
保留其各自许可证；MinIO 为独立 AGPL-3.0 服务，未包含二进制或授予其 Apache 许可。
请查阅 THIRD_PARTY.md。此公开快照排除了私人需求附件、会话和所有运行数据。
