# 本机审计加密归档

`scripts.audit_archive`是本机运维CLI，目前只允许固定的`knowledge-os-acceptance`栈。
导出使用`.env.acceptance`的应用数据库连接，拒绝其他主机、端口、数据库、管理员身份和URL覆盖参数，
核对真实Docker项目标签及回环端口。不会向运行服务注入管理连接，也不执行外部网络请求。

```powershell
./.venv/Scripts/python.exe -m scripts.audit_archive export --acceptance --tenant-id <空间UUID>
./.venv/Scripts/python.exe -m scripts.audit_archive verify --directory <归档绝对目录>
```

交互导出时输入当前账户访问令牌，输入不回显；也支持受控父进程通过stdin传入。
不要把令牌放命令参数、shell历史、聊天或文件。现有会话只保存在账户页面内存，
不为了归档把它写入localStorage。自动化真实验收使用自行创建并清理的测试账户：

```powershell
./.venv/Scripts/python.exe -m scripts.audit_archive_acceptance --acceptance
```

导出复用正常身份、有效会话、账户状态、空间成员和管理员检查。普通成员不能导出空间审计，
非成员和已撤销会话被拒。连接实际current_user/session_user不得super或BYPASSRLS。
在同一只读REPEATABLE READ事务中查询指定空间业务审计及当前账户安全历史；
不包含其他账户或匿名失败桶，不从用户提供的user_id设置身份。
权限按快照建立时校验，之后撤销影响下一次导出，不能撤回已经读取或导出的内容。
当前CLI只支持已核验的本机`EMAIL_VERIFICATION_REQUIRED=false`策略；启用验证后拒绝运行，
避免CLI与运行API政策不同。生产使用方式必须单独配置和评审。

每次最多10000条合计记录、16MiB JSON，单条数据库语句10秒；读取结束时超过30秒则拒绝输出，
该预算检查不是操作系统级硬截止，空闲事务另设10秒超时。
只导出固定动作、UUID、UTC时间，不含邮箱、密码、令牌、内容或模型密钥。
行数超限拒绝整个导出，不截断后误报完整；当前未提供时间范围增量/批量归档。

文件保存在Git忽略的`.cache/audit-archives/<随机ID>`，独占创建，已有目录不覆盖。
明文仅在内存中序列化，用随机AES-256-GCM密钥加密，Windows密钥由当前用户DPAPI保护，
其他系统采用owner-only权限。离线verify限制实际读取大小，完整验证GCM后才解析JSON，
拒绝篡改、错钥、截断、重复字段/事件、未知动作和额外字段。输出只有计数和摘要，不打印记录。
失败保留已有密文现场，不降级明文；读取摘要不能替代实际verify。

归档与密钥在同机保存，不证明独立防篡改存储：持钥者可重新生成有效密文，
DPAPI不提供跨机灾难恢复，summary也不是可信签名。当前不上传归档、不删除源记录、
不自动设置保留期限。独立WORM/对象锁存储、密钥托管、签名/外部锚定、保留/删除政策、
账户删除和异地恢复仍需真实环境及明确策略后验收。

2026-11-07真实CLI/DPAPI、独立进程离线verify及注销后拒绝通过，脱敏报告为
`.cache/audit-archives/469ab698b0c14693b6e0a71629dbbc1e/acceptance-summary.json`。
新增11项离线与2项真实数据库回归通过；当轮完整28项集成/重启、293非集成和Chrome10项通过。
