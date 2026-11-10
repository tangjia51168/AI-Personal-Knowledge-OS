# 对象存储最小权限准备

运行策略模板为infrastructure/storage/runtime-policy.json，部署时把${BUCKET}替换为已创建的私有桶名。
策略只允许该桶ListBucket，以及tenants/*/assets/*的GetObject/PutObject/DeleteObject。
HeadBucket健康检查需要ListBucket，因此运行账户可以列出该桶对象名；不要声称完全禁止列举。
实测固定版本MinIO的ListBuckets还可能返回这个允许的桶，不能用“必须403”假装更严格的权限。

```powershell
./.venv/Scripts/python.exe scripts/storage_iam.py --acceptance
```

脚本先核验隔离验收栈/回环端点，再由本机操作者凭据签发15分钟STS凭证。
凭证只放进程内存，通过实际应用get_s3_client及S3_SESSION_TOKEN配置验证Head/Put/Get/Delete；
列举只能看到目标桶或403，桶策略、前缀外读写和其他桶访问须拒绝。
另签发收窄到随机单租户前缀的临时会话，验证读取自身canary允许、读取另一租户canary拒绝。
只清理本次UUID生成的对象，不修改现有用户、桶策略或命名卷，不持久化临时密钥。
脱敏证据在.cache/storage-iam/<ID>/summary.json；失败不会记作通过。

共享API服务必须处理多个授权租户，模板也允许多个租户资产前缀；租户隔离仍须API成员验证、
DB归属/RLS和服务端key校验，不能把共享S3策略当成每租户授权。收窄的STS会话只是额外实证，
当前服务没有自动为每个请求生成独立租户STS会话。

应用现在支持可选S3_SESSION_TOKEN；使用专用长期运行账户时留空。生产平台须同时注入
S3_ACCESS_KEY/S3_SECRET_KEY/可选S3_SESSION_TOKEN，不把它们与MinIO root或bootstrap账户混用。
临时凭证过期后会拒绝请求；当前适配器不自动刷新凭据，短期测试会话不可直接当长期服务部署。

验收Compose的bootstrap由root创建/检查私有桶；随后使用以下专用受限账户：

```powershell
./.venv/Scripts/python.exe scripts/prepare_minio_mc.py
./.venv/Scripts/python.exe scripts/provision_storage_runtime.py --acceptance
```

脚本使用固定大小/SHA-256的官方mc，在临时非root容器中创建一次随机命名账户，
并保存到Git忽略的`.env.storage-runtime.acceptance`。凭据只经stdin与短期子进程环境传递，
不进入Docker环境配置、命令参数、mc配置文件或日志；Windows权限沿用项目目录ACL。
`scripts/acceptance.ps1`仅先启动bootstrap服务，再带`docker-compose.storage-runtime.yml`
重建API/Worker/Beat，运行凭据缺失即失败，不回退root。重跑只校验已保存身份，不覆盖已有账户。
中途失败保留pending文件；若权限已完整配置，重跑验证通过后原子补记ready，且不执行管理变更。
若账户已存在但权限未完整配置，须由操作者检查后处理，脚本拒绝覆盖。
`scripts/storage_runtime_check.py --acceptance`在三个实际容器中调用应用适配器，验证读写、
资产前缀外/其他桶/桶策略拒绝，并内存检查root及迁移凭据未注入。它不声称现有Celery导出任务使用了S3。
scripts/init_storage.py需要独立bootstrap权限CreateBucket/GetBucketPolicy，不能使用受限运行凭据。
生产须先由管理作业创建/检查私有桶，再向API/Worker注入专用受限账户或带刷新机制的平台身份。
生产应用不得持有root/建桶/策略管理权限，bootstrap秘密不得留在运行容器。

生产仍需实测平台IAM/桶公开访问封锁、TLS、密钥轮换/撤销、凭据刷新、私网端点和备份独立身份。
本机报告`local_runtime_replaced=true`只表示隔离验收Compose的三个运行服务已替换；
`production_runtime_replaced=false`仍表示生产账户、轮换和刷新尚未配置。

固定版本实现参考：[MinIO AssumeRole](https://github.com/minio/minio/blob/RELEASE.2025-04-22T22-12-26Z/docs/sts/assume-role.md)、
[会话权限判断](https://github.com/minio/minio/blob/RELEASE.2025-04-22T22-12-26Z/cmd/iam.go)、
[mc官方发布](https://github.com/minio/mc/releases/tag/RELEASE.2025-04-16T18-13-26Z)。
