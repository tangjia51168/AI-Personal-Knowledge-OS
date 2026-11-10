# 可部署版本与操作顺序

本包提供独立生产运行Compose、严格TLS网关、分离的迁移/运行配置、关闭公网注册下的运维建号入口，以及完整性清单。它不会购买资源、上传镜像、联网部署或生成密钥。现无生产资源，实际域名、生产SMTP、异地恢复、漏洞及独立安全门禁仍未验收。

从压缩包解出后的校验脚本只用Python标准库：`python scripts/release_package.py --verify <zip绝对路径>`，无需安装项目依赖。镜像另行构建/获取，不包含在配置包中。

生成示例包：`.venv/Scripts/python.exe -m scripts.release_package --host knowledge.example.com`。该域名仅示例，不代表已拥有；换成实际获准域名再生成。校验：`python -m scripts.release_package --verify <zip绝对路径>`，仅检查完整性，不提供签名真实性证明。包由固定允许列表生成，不含.env、用户文档、数据库、模型权重或镜像。哈希清单防意外损坏，不能代替可信签名。

## 所需资源及镜像

- 已有代码同一版本构建API（包含Worker/Beat/Alembic/建号CLI）、Web、网关镜像；使用原Dockerfile和强制依赖哈希。通过镜像扫描后发布到私有注册表，记录不可变`repository@sha256`。本轮未上传；运行包`pull_policy: never`要求运维先显式获取已审核镜像。
- PostgreSQL16+pgvector：独立数据库和knowledge_app登录角色，NOSUPERUSER/NOBYPASSRLS/NOCREATEDB/NOCREATEROLE，不属于管理员；另有仅迁移时使用的管理员身份。首次初始化参考原infrastructure/postgres/init-app-role.sh，密码通过受保护变量提供，不进命令历史。不得将本机验收库当生产库。
- 带认证的Redis，优先rediss并验证证书；私有HTTPS S3，专属桶、无匿名、受限对象身份，按docs/storage-runtime-policy.md配置。运维root凭证不得注入应用。
- 已授权域名、有效完整证书链/私钥，文件可由UID101网关读取。网关默认只绑定127.0.0.1:443；实际放公网前需全部发布门禁通过。API/Web/DB/Redis/S3管理端口无宿主端口。

## 配置、迁移与启动

需要Docker Compose>=2.24.4。生产文件**单独使用**，不要叠加开发/验收Compose。将runtime.env.example、migration.env.example、compose.env.example分别复制为受保护文件；秘密放仓库外，Windows限制ACL/Linux0600。runtime不得含MIGRATION_DATABASE_URL；Compose还显式移除该字段。migration只提供迁移连接，不继承运行秘密。

填写实际URL/域名与互相独立的强随机密钥，URL密码须percent编码；JSON keyring为32随机字节的base64。SMTP未配置时只能站内通知；邮件提醒需本人已验证邮箱。开启EMAIL_VERIFICATION_REQUIRED需要实际认证TLS SMTP和HTTPS地址，不将验证邮箱位直接设true。付费Provider、采集及本地端点默认关闭，沿用应用内逐次授权。

以下`<compose.env>`为绝对路径。先完成备份与空库/角色准备，再逐步执行；不能将带秘密的`docker compose config`全文贴到日志，仅使用`--quiet`。先运行标准库输入检查，拒绝可变镜像、运行/迁移文件混用或迁移凭据进入runtime。文件使用字面KEY=value，不使用export、外层引号、插值或多行；JSON值保留内部引号。执行Compose的终端不得预设KOS_*同名变量覆盖文件。检查不会联网、启动容器或验证秘密有效性。

```text
python scripts/release_config.py --compose-env <compose.env>
docker compose --env-file <compose.env> -f docker-compose.production.yml config --quiet
docker compose --env-file <compose.env> -f docker-compose.production.yml --profile maintenance run --rm --no-deps preflight
docker compose --env-file <compose.env> -f docker-compose.production.yml --profile maintenance run --rm --no-deps migrate
docker compose --env-file <compose.env> -f docker-compose.production.yml up -d --wait api worker beat web gateway
docker compose --env-file <compose.env> -f docker-compose.production.yml --profile maintenance run --rm --no-deps provision
```

preflight禁网，仅验证配置。真实API/Worker启动另验数据库角色和最新0031/RLS及依赖；迁移固定到0031，不从ORM生成历史迁移。provision交互输入邮箱及两次密码，复用正常RLS/成员/审计，不输出密码或登录token，邮箱保持未验证。它不是公开API。邮箱验证启用后，可在账户页申请验证邮件；首次登录完成后同一会话访问全部组件。需要恢复旧用户时，应从已验证备份恢复而不是重复建号。

## 验收、备份与回滚

输入检查还拒绝终端预设的任何KOS_*或MIGRATION_DATABASE_URL，防止Compose优先级覆盖已核对文件或null字段继承管理员秘密。请在干净的专用部署终端运行所有步骤，检查后不要改变环境或配置文件。启用向量overlay时输入检查追加`--embedding`。

本地向量功能可显式追加`-f docker-compose.production-embedding.yml`（每条Compose命令保持相同文件组合），提供KOS_EMBEDDING_IMAGE固定digest与KOS_EMBEDDING_MODELS目录。权重不包含在本包，须按infrastructure/embedding/model.json提供经哈希核验的固定ONNX/tokenizer；构建原embedding.Dockerfile，启动自动验证。该服务无公网端口/外网出口，只API可访问；不启用时语义检索应明确不可用，字面检索仍可使用。模型授权/长度限制见docs/semantic-search.md。

生产环境先用专门验收账户验证TLS/Host/诊断阻断、登录期限/撤销、租户隔离、私有文件、后台到期站内提醒和本人邮件自测。SMTP接受不等于收件箱送达；SPF/DKIM/DMARC和真实邮箱收取须另留证据。后台日志仅运维可见，保留轮转限制，不公开容器或连接信息。

本轮本机基线及失败报告见PROGRESS。生产不能运行`acceptance.ps1`或测试夹具；它们固定为隔离本机项目。本机45表/收藏/通知回执/向量/长文检查点恢复不替代生产异地演练。部署平台须配置独立加密PG/S3备份及密钥托管，并在单独目标恢复，核对schema/FK/RLS/触发器/正文哈希和对象哈希，记录真实RPO/RTO。

更新前备份，记录原Image digest与schema。只做经评审的追加迁移；不自动降级数据库、删除卷或恢复覆盖原实例。回滚优先切换到独立恢复目标，确认兼容性后再换应用镜像；不能假设0031可无损回退旧schema。没有现成生产备份/切换命令授权时，不自行连接云资源。

本模块不将完整12条主线标记完成；生产资源、漏洞/许可/签名、安全评审和外部送达继续作为发布门禁。可部署包也不包含第三方客户端安装或个人设备接入。

当前运行镜像的实际漏洞发现与升级/重建维护入口见`docs/runtime-vulnerability-followup.md`。未经固定digest复扫通过的镜像不能作为公开发布工件。
