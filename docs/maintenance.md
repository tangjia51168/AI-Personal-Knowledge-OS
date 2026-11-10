# 模块维护约定

可部署版本维护入口：`docs/deployable-release.md`；独立Compose/固定schema迁移/最小权限/秘密分离/域名模板/包允许列表与哈希、防篡改校验和交互建号持续回归。标准库`release_config.py --compose-env <绝对路径>`拒绝可变镜像与秘密混用，含embedding时追加`--embedding`。`python -m scripts.release_acceptance --acceptance`检查真实解析器、禁网生产预检及建号CLI，不部署生产；真实建号测试入口release主线integration，仅专用验收库。

镜像复扫固定实际ImageID，包含运行与退出的验收/恢复/专项测试容器，命名必须符合服务及UUID规则；不输出Env或上传镜像。最多3个并发Scout调用，运行镜像优先，主线程独占summary写入；最后重读清单检测漂移，缺结果/命令失败/漂移均拒绝放行。历史漏洞与运行镜像分别给门禁，不能删除现场或忽略无修复版本来过关。并发及清单回归`pytest tests/test_scan_runtime_images.py -q`。

提醒通知维护入口：`docs/notifications.md`；主动订阅/版本、固定到期防重、RLS本人隔离、审计原子、SMTP持久发送声明/未知阻重发与后台撤权复验均需回归。本机SMTP不计外部邮箱送达。

本地多语言向量维护入口：`docs/semantic-search.md`；模型/分词器digest、CPU真实推理、pgvector精确修订/租户RLS/位置约束、最新版本失效、同版本复用、索引配额与失效清空需要持续回归。无向量服务不能记真实集成通过。

固定修订证据自测维护入口：`docs/evidence-recall.md`；复用导出绑定/纯文本/31卡上限、自评重练与失败清空，页面内存结果不得当作永久学习历史。

统一12小时会话与图标导航维护入口：`docs/account-session.md`；Cookie/Origin边界、绝对过期、撤销、多标签页、草稿与原组件浏览器回归必须持续保留。

适用于现有及后续全部模块。此约定是持续验收要求，不意味着已对全部历史代码完成专项维护审计。

## 每次变更必须保留

- 业务在 `apps/api/app/modules`，外部系统边界在 `integrations`；Web只展示和同源代理。优先复用，避免无消费者模块和跨模块大重构。
- 明确请求/响应、授权顺序、数据归属、错误/重试与资源限制。接口变更同时修改BFF白名单、前端契约和回归用例。
- 身份/成员、租户上下文/RLS、版本、并发/撤权/注销及输入边界按风险覆盖。真实DB/Worker/对象存储和浏览器检查不能以mock替代。
- 新迁移冻结且追加，不修改历史迁移；无破坏性回滚。无schema变化不声称重新恢复成功。
- 测试进入统一检查或固定集成入口，不只留在忽略的临时脚本；说明可复现命令、失败证据、已知限制及恢复思路。
- 每次同步PROGRESS/TASKS/必要CHANGELOG、架构/功能说明与进度看板；只在实际通过后增加已验收成果。新镜像扫描和外部实测不能沿用旧证据。

## 固定检查入口

第一版工作区及连续流程见[v1-product.md](v1-product.md)；问答见[research-answers.md](research-answers.md)，公开来源计划见[source-schedules.md](source-schedules.md)。新0025–0027已应用冻结，当前38表；独立实例恢复包含人工首篇、固定引用问答记录及暂停计划，旧0024的33表证据不能替代。

架构主线维护与异地接入见[foundation-infrastructure.md](foundation-infrastructure.md)。固定入口foundation_recovery执行独立实例/冷队列/业务与有界性能检查，保留源数据和所有新卷；operations_check消费受操作员保护的脱敏指标。foundation_audit离线检查锁文件并盘点许可，不能把元数据清单当法律或CVE批准。

人工概念卡片契约见[concept-cards.md](concept-cards.md)，0024新增表与证据复合键须真实迁移/租户事务/浏览器和新schema加密恢复，旧0023证据不覆盖。0025人工首篇已真实迁移/租户事务/浏览器及38表恢复；不可变人工定义不冒充全库概念目录或方向关系。

首篇人工笔记的证据预览由`GET /snapshots/{id}/note-evidence`提供服务器切分的有限片段；前端不得猜测编号。创建请求保留幂等键，未知结果原键重试；迁移0025应用前不得启动新API。导出中心复用固定修订读取和现有单篇/批量渲染，不另建导出数据表。

主题层级维护见[主题层级](topic-hierarchy.md)，0023新增schema须重新真实迁移/集成/恢复；恢复指纹覆盖非内部触发器及其函数定义/执行配置/权限，不能沿用0022证据。

12主线目标、来源、缺口和整体规则见[主线验收基线](mainline-acceptance.md)。同步track-acceptance.ts和project-progress.ts；recorded范围不推导整体delivered。`scripts/track_acceptance.py --track <id> --list`列出集合，`--run static|integration|browser`只验已实现范围，空集未验收；详细环境/局限见基线。
该浏览器入口使用现有验收Web同镜像的临时只读/无卷/loopback容器隔离来源IP；组织主线同时保持两个不同来源Web，层级用例独立origin，完整JSON统一汇总。记录/排除历史使用IP及网络网关/活动容器，不清共享限流桶；ExitStack结束停止自身容器，JSON报告拒绝跳过/失败/flaky。不要与集成/重启并行。错误报告保留本地，不打印连接串或凭证。

本机依赖使用`.venv`和workspace `node_modules`。`./scripts/check.ps1`覆盖Python lint/格式/mypy/非集成、Node契约测试、TS/lint/生产构建。
真实集成使用独立验收库/栈：`./scripts/acceptance.ps1 -Hardened -RestartCheck`。该操作会重启本机隔离依赖，不与浏览器测试并行。
浏览器由`scripts/account_e2e.py`创建和清理自身协议账户；入口环境须指向隔离服务，不打印凭证或开启含令牌trace。
交付按项目主线组织连续流程与剩余范围，模块保留为维护单元；看板`trackModules`映射必须只引用真实成果。当前知识组织与检索的契约/维护入口见[主线说明](knowledge-organization-track.md)。
只读运维：`.venv/Scripts/python.exe scripts/operations_check.py --acceptance`；本机TLS：`scripts/tls_gateway.py --acceptance`。

## 当前维护边界与暂缓

2026-11-08用户允许进入下一模块，并暂缓上一轮外部/完整备份相关验收：分享页全文获取、第三方Obsidian/演示软件客户端实测，以及便携完整空间包尚未实现部分的验收。保留未完成，不安装客户端、不访问用户vault，不以现有DB/S3运维备份替代便携空间包。
这不是暂缓所有新模块测试，也不解除生产漏洞、异地恢复或权限门禁。邮件/推送、便携空间包功能开发仍为后续范围。

新模块维护入口和证据见各自功能文档；历史功能见docs/module-delivery.md及TASKS/PROGRESS。后续修改旧模块也按同一清单补齐，不承诺永不发生缺陷。

2026-11-09隔离Redis启动发现AOF尾部损坏：原`knowledge-os-acceptance_redis_data`只读复制后保留，官方工具仅修复恢复卷。验收Compose通过`.env.acceptance`的可选`ACCEPTANCE_REDIS_VOLUME`选择恢复卷，默认仍为原项目命名；禁止直接修复原卷、清空Redis或删除卷来冒充持久化通过。具体检查/风险记录见PROGRESS。
## 长文与工作台增量维护

2026-11-10：新增长文质量/断点维护见long-analysis.md。`summary_checkpoints`是私有本人阶段与费用关联，不允许手工清pending或将dispatched重置来恢复；应读取快照本人生成历史/检查can_resume，只继续未发出的请求。进程在外部调用期间中断无法证明是否收费，仍阻重发。0028后必须使用新镜像、新数据库恢复指纹和真实集成；旧38表恢复不是新表验收。用户现已解除旧暂缓，公网/真正异地等须实际资源，先可部署准备；旧历史记录保留时间语义。

`docs/long-analysis.md`明确每阶段授权/费用、固定快照/证据、整体保存和失败/未知边界；`docs/reference-workspace.md`记录公开参考来源、布局断点与真实数据语义。统一check、理解主线集成/浏览器为回归入口；真实付费检查必须显式启用，不纳入自动回归。无schema改变不重复无变化恢复；当前镜像生产漏洞门禁仍独立。

# 收藏与公开源码维护增量

共享收藏/合集的版本、保留关联配额、归档和来源边界见collections.md，真实数据迁移0031冻结。新模块同样执行租户/成员/RLS/原子审计与接口回归。完整新M01–M10规格见spec-alignment.md，不将阶段完成推导整条主线完成。

源码候选由`python -m scripts.source_release`生成，`--verify <source>`检查精确清单与内容；私人需求文档/会话/环境/运行工件不得公开。GitHub发布只用独立快照，`python -m scripts.github_publish --source <source> --publish-authorized`必须已有明确发布授权；固定账号复验、既有仓库拒绝覆盖、无强推、不写令牌。扫描是有限规则，不能替代人工审查。第三方独立服务/模型不随源码重新许可。

