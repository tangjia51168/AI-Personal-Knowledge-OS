# 架构基线 V1

2026-11-10收藏增量：organization/collections维护空间共享分组和来源关联，不复制Source/Note。0031新增两表，复合FK/FORCE RLS/列权限保护租户及成员；组版本统一覆盖元数据与成员变更，原子审计、归档保留与有界配额。API/BFF/独立控制台共用现有身份会话；平台同步另列待实现。新M01–M10规格映射见docs/spec-alignment.md，不重建现有模块。

2026-11-10可部署增量：独立production Compose不叠开发/验收配置；API/Worker/Beat无迁移凭据且关闭公开注册，显式maintenance迁移固定0030。标准库release_config检查字面配置/镜像digest/运行迁移文件分离；release_package按允许列表打包和无提取校验，不含模型/镜像/秘密。identity/provision复用原身份RLS与原子审计，交互建号不发token、邮箱不冒充验证。真实Compose/Nginx/禁网预检和建号集成已通过；无生产服务启动，资源及生产安全仍为独立门禁，见docs/deployable-release.md。

2026-11-10通知增量（本模块已验收）：learning/notifications复用学习到期与个人身份边界；0030本人订阅/回执FORCE RLS、复合FK，无正文UUID调度索引供Beat发现，Worker从持久关系重验。站内通知/自测/已读有实际Web消费者；SMTP与业务分离，持久claim提交后不持锁发送，未知禁止自动重发。169真实集成/重启/S3、Chrome4/43表回执恢复通过；维护契约docs/notifications.md，不宣称外部邮箱送达。

2026-11-10向量增量（本模块已验收）：notes/semantic显式固定修订本地索引与pgvector精确余弦检索，0029仅追加不可变semantic_chunks，复合FK/FORCE RLS/精确位置约束/管理员追加；模型通过integrations/embedding固定内网边界。CPU ONNX独立服务、固定工件哈希、无外网出口；Web查询/覆盖统计/固定修订读取与详情确认索引。每批及提交重验身份、无DB锁跨推理；165集成/Chrome2/40表实际向量恢复通过。不自动云回退，不代替字面/RAG，限制见docs/semantic-search.md。

2026-11-10增量（本模块已验收）：0028_summary_checkpoints复用notes和AI边界，私有本人RLS/复合FK/列授权，逐阶段预留与dispatch原子、结果与真实用量原子；只复用成功阶段，已发出未知不自动重发，继续显式费用同意/逐次重验。快照本人最近20生成记录用于刷新后恢复。质量纯函数拒模型处理数量声明及引文中不存在的阿拉伯数字；不自动改写模型输出，不证明一般语义正确。真实DB/Chrome/付费样本及39表独立恢复通过，证据以PROGRESS为准。

长文总结在notes内纯函数分段/综合契约与已有service编排；每阶段复用Provider/用量授权，失败停止、未知阻重试，全部成功后原子保存v2不可变修订及分段记录/所有证据，无新schema。人工改写只沿用证据范围，旧分段留在原版本。见docs/long-analysis.md。工作区参考布局只重用现有消费者，见docs/reference-workspace.md。

证据自测为已有固定修订JSON导出的只读消费者，复用readerDocument/readerEvidence校验，按现有条目生成回忆卡；页面内存自评及待练子集，不写学习状态、不调用模型或新增schema。见docs/evidence-recall.md。

2026-11-09增量：统一12小时绝对会话由Web BFF HttpOnly Cookie保存，API仍逐请求Bearer/成员/RLS核验；浏览器不读取Bearer，登录和Cookie写请求校验自定义标记/Origin，注销/401/到期清空工作区，见docs/account-session.md。图标导航仅展示当前区并保留其他区草稿；本段取代下述旧内存会话说明。

第一版工作区复用现有模块，10组件对登录成员可发现；管理员消费者按角色挂载，后端权限保持；主题偏好持久化而Bearer仅在内存。文本文件在本机UTF-8预览，确认后走原source_import/outbox/Worker。人工首篇0025及导出中心复用修订/证据/审计，真实服务/Chrome和38表恢复已验证。历史“0025待验”由最新PROGRESS更新。

0026基础RAG复用最新修订字面检索与固定证据，research_runs/research_citations保存本人幂等结果及不可变引用；独立授权事务、用量预留/实际记录、未知不重试；GET本人最近20条，无证据不调用模型，有证据仅DeepSeek单次请求。契约docs/research-answers.md，本轮无新付费推理。

0027公开来源计划在sources，Beat另每30秒扫描最小全局ticks；Worker复验计划/成员后复用source_collect，暂停与提交串行化；仅最新快照完整语义相同才复用，变化追加。不是收藏夹账号同步或模型自动流水线。契约docs/source-schedules.md；原Beat每5秒outbox职责保持。

架构主线维护入口`docs/foundation-infrastructure.md`：独立实例恢复复用AES-GCM归档与全库指纹，重建非bypass角色，恢复私有S3、会话/文件与冷Redis outbox，由独立Beat/Worker执行；新卷/失败现场保留、原服务回切。API进程内有界路由指标仅操作员可读，由operations_check实际消费；重启清零，不是分布式链路或外部告警。foundation_audit检查精确依赖/hash并盘点许可，法律/CVE门禁独立。真实异地/生产目标仍未验收。

人工概念卡片：organization消费者复用notes固定修订及证据，0024追加不可变knowledge_concepts/证据四元组FK、FORCE RLS与仅追加权限；GET/POST、幂等/配额/锁后复验及原子审计，Web显式读取/确认/同键核对。147项历史集成及含概念数据恢复通过；后续人工首篇0025及38表恢复也已验，最新记录见PROGRESS；不是全库概念/方向关系，契约docs/concept-cards.md。

主题层级复用organization目录：0023 nullable parent_id与同租户同类型FK，invoker触发器有界祖先检查/行锁；现有授权租户锁先行、版本/审计保持。管理GET/PATCH与Web完整路径消费者，精确分类筛选不变；恢复指纹追加trigger/function定义、权限和执行配置。141项集成及新目标恢复/Chrome主线通过，维护入口docs/topic-hierarchy.md。

2026-11-09：全12主线需求/验收扩展见docs/mainline-acceptance.md。需求清单是静态工程契约，由看板消费，不访问租户/日志/凭证；主线验收脚本仅复用固定检查/隔离服务测试。空间人工图交互为Web局部视图状态，保持原API/RLS/修订/截断契约，不新增schema或推断关系。

## 内容到笔记链路（当前增量）

知识组织与检索主线复用notes与organization边界：knowledge/search按最新修订联查真实证据/批注，租户/修订/快照显式绑定，PostgreSQL绑定参数lower/strpos字面匹配，5秒事务内查询上限；结果携带精确修订ID与240字符片段。knowledge/graph读取30节点/60边的空间人工图（含孤立节点、明确截断），不推断关系。GET/BFF/租户成员/RLS与读取后会话复验，Web复用原阅读/证据组件，无schema/依赖/外部调用。维护见docs/knowledge-organization-track.md。

人工分类目录维护复用organization授权事务与租户写锁，GET labels/manage有界读取含归档目录；PATCH labels/{uuid}以expected_version锁后比较，实际变更递增版本/更新时间/当前成员updated_by，与审计原子提交，无变化不写。0021追加状态/版本/时间，0022追加更新者租户复合外键与RLS当前成员检查；归属、类型、创建者不可改，无删除权限。归档保留历史关联/筛选及名称配额，禁止新关联与隐式恢复；独立Web消费者确认/失败清空/切换取消，见docs/label-management.md。

个人复习工作台从日程选择精确笔记，复用详情/预览/NoteReader；review PUT显式状态与提醒，expected在既有update事务/租户锁后验证，冲突409，成功与审计原子保存最后完成时间。无schema/队列/依赖，旧learning PUT兼容；不记录次数历史或自动排课。见docs/review-workbench.md。

个人复习计划复用批量选择/预览与0020：前端本机日历按勾选顺序/每日篇数/间隔天数分配明确UTC时间，最多20篇/90日历天，逐篇确认；精确plan PUT接受每篇时间，复用同一租户授权写锁/expected整批校验/原子审计/no-op。服务端限制91连续天兼容DST，不接收排课规则；无schema/队列/外部调用，不存命名计划或重复排课规则，见docs/review-plan.md。

批量提醒复用0020，以明确1–20篇预览生成个人记录摘要，PUT整批校验expected再写入；现有authorized_transaction租户锁/成员/RLS及锁后会话复验保证并发串行化。只改提醒，保留状态/完成时间，无变化不写，每实际修改与审计原子提交；未知结果要求重新预览核对。无schema/队列/外部调用，消费者跨页选择和确认，维护见docs/reminder-batch.md。

个人复习日程复用0020，身份/有效成员与会话复验后，一次有界联查读取当前用户提醒及最新标题。due或未来7/30/90连续天按UTC截取，提醒时间/UUID排序，最多100条/明确截断。ICS同授权重新读取，截断拒绝，纯文本转义/UTF-8折行/UTC/稳定UID与256KiB限制；无自动闹钟、账号/设备调用或业务写入。独立消费者切换取消/清空/手动重试；维护见docs/learning-schedule.md。

个人复习清单扩展已有notes列表，左联查当前用户note_learning并显式绑定租户/笔记/用户。四状态/未记录与到期/未来可组合现有筛选；最新修订后按笔记创建时间+UUID有界分页。无新schema，一页一查询/游标两查询；API/BFF拒绝非法个人筛选与用户覆盖。状态保存清旧清单、读取失败清空/重试，维护见docs/learning-review.md。

个人学习总览复用0020，身份/有效成员后用一条SQL在notes上左联查当前用户note_learning，并显式绑定租户/笔记/用户；按笔记身份计数、不受列表分页影响，无新schema或业务写入。严格响应模型与Web契约检查计数不变量；无记录计未看且单独披露，不生成掌握率。独立组件切换取消、失败清空和手动刷新；维护入口docs/learning-summary.md。

所有模块的可维护性持续要求见docs/maintenance.md：接口/数据语义、回归、冻结迁移、维护命令、已知限制与暂缓记录必须随修改更新，不以一次测试成功承诺无缺陷。

结构化阅读复用不可变修订及既有授权JSON导出；前端验证笔记/修订/快照ID及所需证据编号，
按纯文本显示章节/缺失/范围/个人理解，目录和引文仅在阅读区内定位，不解析HTML或自动访问原链接。
切换卸载取消旧请求，错误清空证据；无后端/schema变化，见docs/structured-note-reader.md。

`/progress`为随Web构建的人工工程记录，静态消费固定字段和账户入口，不读取租户数据/本机日志/环境秘密。
主线与模块成果分别记录范围、历史验收基线和未完成项，不估算百分比；与实时健康检查分开。

学习状态与提醒使用 `note_learning` 用户-笔记关系：状态和提醒时间按成员隔离，事务内设置租户上下文并由 FORCE RLS、复合外键和有效成员策略保护。到期查询是有界只读查询；系统当前不包含邮件、浏览器通知或推送发送器，因此只报告真实到期记录。
个人复习分析复用该关系执行单条租户/成员绑定只读聚合，使用服务器UTC统计当前 `completed_at` 的近7/30天窗口。`completed_at` 仅表示最后一次非未看保存时间，不代表复习事件历史；系统不推导复习次数、连续学习天数、趋势或掌握率。维护契约见docs/learning-analysis.md。
固定账户锚点与邮件令牌片段分流，登录后定位，原邮件令牌移除/手动确认不放宽；见docs/module-delivery.md。

人工链接局部图谱复用0019关系、成员授权与FORCE RLS，不新增schema或推断关系。
一次有界有效边查询及最新修订联查，确定性一/两跳BFS，30节点/60边、连通树优先并明确截断。
精确GET BFF与手动SVG/无障碍列表消费者，无模型/平台请求，见docs/manual-note-graph.md。

单修订导出复用成员授权与现有0019 schema，所有联查显式绑定租户/笔记/修订/来源/快照。
Markdown按原字节导出，JSON显式字段及摘要，纯文本从结构化章节生成，HTML完整转义且无主动资源。
精确GET BFF、attachment/no-store/nosniff/sandbox、1MiB输出上限；不写DB或调用模型/平台。
仅单版本及引用证据，不是完整备份，见docs/note-export.md。

固定修订批量导出通过精确POST BFF提交明确选择的笔记/修订对，最多20个，任一授权/绑定失败整包拒绝；不自动选择最新版本。复用成员/RLS和导出查询，每修订1MiB，总包含清单8MiB。UUID根路径wiki链接消除多笔记同名目标歧义，清单逐文件SHA-256；无本地vault/插件访问。PPTX/SVG仅纯文本OOXML/原生SVG，XML禁止字符拒绝，流程图只表达原声明步骤顺序，不推断关系；客户端兼容性与格式守卫分开验收。

0019人工笔记链接按两个笔记UUID规范化为一条无方向状态记录，关联整篇笔记而非修订。
同租户复合外键、FORCE RLS和最小列授权约束双端/操作者；授权事务串行化配额并原子写审计。
关系方法固定manual、置信度为空，不推断支持/反驳/因果。见docs/manual-note-links.md。

0018人工批注绑定不可变修订/章节/索引和原段引文；追加授权事务校验原文、操作者、幂等与配额，
FORCE RLS独立检查成员与原文匹配，复合外键绑定同租户的笔记/快照/修订和作者；与审计原子提交。
批注不改变正文/Markdown，不跨版本自动迁移。见docs/note-annotations.md，当前验收状态见PROGRESS.md。

来源持久化后可显式提交 source_collect → DB outbox → Redis → 独立 Worker → 0013不可变快照。
平台连接集中在 integrations/platforms：固定公网DNS/IP连接、逐跳校验、TLS、响应大小与总时限，
不上传Cookie/加载页面脚本。网页提取正文，B站/YouTube初版仅元数据；部署可达性按真实请求记录。
0014知识模块通过显式同步总结接口消费真实快照，DeepSeek Provider输出有界结构化文档，
证据编号验证后同源生成结构化章节与Markdown，按版本追加，未知付费结果不自动重试。
API/Worker均复核成员与任务归属，所有新增业务表FORCE RLS/租户复合关系。
0016笔记人工修订通过成员授权事务追加版本，基准版本冲突拒绝覆盖；幂等键绑定请求摘要与操作者。
人工版本无AI run，证据与范围说明由服务器重建，个人理解独立标明未经核验；旧版本只读保留。
具体边界见docs/note-editing.md，真实验收仍以PROGRESS.md最新记录为准。
来源/笔记列表复用身份与租户上下文，以不可变创建时间+UUID进行有界游标分页；
笔记先选最新版本再筛选，以有界联查读取列表、另行校验游标锚点，避免逐笔记版本查询。来源类型共享平台域名词表，
不执行DNS或采集；精确GET的BFF查询参数白名单见docs/source-note-filtering.md。本模块无schema变更。
0017新增人工主题/标签消费者，knowledge_labels追加保存空间分类，note_labels关联到笔记而非修订。
0017时分类定义只追加，后续0021/0022已支持版本化重命名/归档恢复；关联以active状态切换并绑定最后操作者，
FORCE RLS检查有效成员，租户复合外键绑定笔记/分类/成员。授权事务串行化配额与同事务审计。
按label_id的EXISTS筛选可组合最新版本条件且不重复行，页面提供创建/关联/移除和筛选；
不会调用模型或推断语义关系；主题层级与固定修订概念卡片已单独交付，全库概念/合并与方向关系仍待实现。目录维护与人工局部/空间图范围见上文。见docs/manual-note-labels.md。
实现与局限见docs/content-knowledge-pipeline.md；真实验收见PROGRESS.md最新节。

## 决策

多租户 SaaS，Monorepo、模块化单体、独立 Celery Worker。Next.js + TypeScript；
Tailwind CSS；Python FastAPI、Pydantic、SQLAlchemy、Alembic；PostgreSQL + pgvector；
Redis + Celery/Beat；S3 兼容对象存储。Chrome MV3 与 Local Agent 独立保留边界。
未来按实际页面引入 shadcn/ui、Motion、ECharts、Cytoscape.js，当前不安装无用途依赖。
AI 使用自研 Provider 边界，已加入 OpenAI Responses、DeepSeek 和 Ollama；vLLM 尚未实现。
凭证/偏好/路由已真实数据库验收，DeepSeek短请求及云端失败回退成功；Ollama仍未通过（见 PROGRESS.md）。

## 当前运行链路

可选本机TLS入口：Nginx回环14443 → Next.js/account及同源BFF → FastAPI。
网关阻断诊断与直连API，真实TLS/限流/Host/体积边界已验收；生产证书/网络仍未验收，见docs/tls-gateway.md。
S3适配器支持可选会话令牌，运行策略限制私有桶与租户资产前缀；STS真实验收见docs/storage-runtime-policy.md。
共享运行账户仍须由API/DB执行租户授权，不能将S3前缀策略视为业务隔离。生产bootstrap与运行身份须分离。

```text
Browser → Next.js /api/system → FastAPI /health/live + /health/ready
                                           ├─ PostgreSQL（迁移、角色与 RLS 检查）
                                           ├─ Redis PING
                                           └─ MinIO 私有 bucket HEAD
Operator → protected /api/v1/system/smoke-jobs → Redis → Celery Worker → Redis result
```

前端同源诊断代理不会携带操作令牌，只返回已过滤的服务状态。
健康检查不返回配置、连接串、异常正文。liveness 不依赖外部服务，readiness 失败返回 503。
Celery smoke task 无用户参数，不操作租户数据。它本身不能替代持久化业务任务及其授权验收。
Beat每5秒发布DB outbox扫描，另每30秒发现到期来源计划；实际平台采集由重新授权的Worker执行，Beat不直接调用平台或模型。

## 持久化任务（已真实验收）

`POST /spaces/{id}/jobs` 在身份/成员验证后锁定租户行，以同一事务完成幂等查重、
活动/每日配额预留、任务/步骤和 outbox 写入。API 不依赖 Redis 发布成功才保存任务。
Beat → dispatch task 扫描全局最小 job_dispatches（仅 UUID/时间，无业务 payload），
发布 opaque dispatch ID；不使用管理连接或 bypass RLS。消息至少一次发送，30 秒后未完成自动重发。
Worker 从 DB 找实际请求人 → 验证用户/有效成员/空间 → 设租户上下文 → 锁定并复核实际任务归属。
持久化 90 秒租约，Worker 时间上限 60 秒；中断后重放最多 3 次，记录 attempt 和步骤。
业务消费者包括有界空间元数据导出、公开来源采集与人工正文/字幕导入；导出/导入的DB结果原子提交。
外部采集在网络前后分别授权，不是通用外部副作用幂等保证。
取消与最终提交串行化；成员停用同时取消未完成任务，保留历史复合外键。
每空间最多 10 个未完成/100 个当日任务；任务结果只对仍有成员权限的原请求人开放。
UI /account 可创建/手动刷新/取消/重试/查看真实 JSON 结果，不模拟进度。

## 代码与数据边界

- apps/web：展示与同源 API 代理，服务端读取 API_BASE_URL，不接受用户传入 upstream URL。
- apps/api/app/modules：业务模块；main.py 仅组合路由/全局错误与中间件。
- apps/api/app/integrations：外部存储、消息、凭证加密、AI Provider与平台安全连接器。
- apps/api/app/db：SQLAlchemy 模型与 tenant_session；migrations 使用独立冻结版本。
- apps/worker/worker：独立部署，共享 API 核心包，禁止依赖前端。
- packages：以后按实际需要增加协议/客户端/模板；OpenAPI 是 API 契约真源。

## 租户与身份

0007_auth_recovery加入邮箱验证与密码重置；令牌只存哈希、用途/过期绑定，User锁后重验并单次消费。
密码重置与登录串行并撤销全部会话；邮件SMTP边界强制生产TLS，不下发秘密到队列。
请求统一202后同进程后台发送，失败需用户重发；外发送达未验收，生产注册仍关闭。详见docs/account-recovery.md。

用户不是租户；身份与空间通过 tenant_members 关联。授权顺序：验证身份 → 查询成员权限
→ 确定租户 UUID → 在事务内 set_config(..., true) → 带 tenant_id 查询 → 提交/回滚。
禁止将 X-Tenant-ID 或队列 payload 直接视作授权。Worker 必须重新检查任务归属和成员权限。

应用角色 knowledge_app 非超级用户，无 BYPASSRLS、不拥有业务表、无 schema CREATE 权限。
迁移角色仅用于 bootstrap/migrate，API、Worker、Beat 不持有迁移密码。
租户表 FORCE RLS，无上下文/空上下文默认无数据；users 用 app.user_id 自身访问策略。
tenant_session 仅是底层原语；业务使用身份/成员授权后上下文。写操作统一 tenant→member→job 锁序，
锁后刷新 ORM 权限；文件/AI 外部 I/O 之间使用独立授权事务，不跨网络持锁。
身份模块已加入 /auth 与 /spaces：opaque token 只存 SHA-256，12 小时过期，注销立即撤销。
身份索引 auth_identities 只存邮箱与 user_id、不存密码，作为全局认证边界；密码仍受 users 自身 RLS。
auth_sessions 是全局认证记录，不视作租户业务数据；运行角色仅授予查询/插入/删除权限。
成员发现策略只允许已验证 app.user_id 查询自己的空间，业务操作必须先验证成员再设 app.tenant_id。
身份事务用 function-scoped dependency，在响应发出前提交，避免成员移除/注销短暂失效。
真实身份验收与模块状态见 PROGRESS.md；不要将初始化验收当作完整产品授权验收。

## 存储

私有 bucket，服务器生成 tenants/{tenant UUID}/assets/{asset UUID} key。
客户端不得直接指定存储路径。授权下载先验证数据库资产归属，再签发短期下载票据。
文件 API 已真实验收：原始请求体上传，10MiB 上限/30 秒接收时限，临时文件内存阈值 1MiB，
空间上限 100MiB/1000 文件；UTF-8 文本、PDF/PNG/JPEG 基本签名验证，不是病毒扫描或完整格式解析。
事务先预留 pending 行与配额，再写对象并复验身份/成员后 ready。中断保留 pending，15 分钟后可删除。
删除先持久化 deleting，再 S3 delete，最后删 DB 行；失败可重试。不提供任意用户 key/路径输入。
60 秒 HMAC 票据绑定 tenant/asset/user/有效期，下载仍验证身份与成员/资产 DB 归属，
服务端代理流式返回 attachment/no-store/nosniff，不向客户端泄漏存储 key/root 凭证。
使用专用 DOWNLOAD_SIGNING_KEY 与域分隔签名，轮换使当前 60 秒票据失效；无公开 bucket URL。
基础Compose中MinIO root凭据仅供本地开发；隔离验收通过storage-runtime覆盖向API/Worker/Beat
注入专用最小账户，root仅供MinIO/一次性bootstrap和操作者检查。公开环境仍需独立IAM与轮换。

## AI 设置与调用边界

固定 0005_ai_settings 存储 tenant RLS Provider/usage 与用户空间偏好。AES-GCM 随机 nonce，
AAD 绑定租户和供应商 ID，版本 keyring 支持重新加密；停用清除密钥。
连接测试由管理员逐次同意，每个阶段独立复验会话/有效成员/管理员；网络前持久化每日配额预留。
最多两个按优先级的配置、每次 25 秒总期限/64KiB 响应；未知结果保留 reserved 并停止回退，
避免重复付费。本地转云另需用户偏好，云端运维默认关闭；提示词和回答不持久化。
HTTP仅400/401/403/404/405/406/413/415/422/429视为明确拒绝；其他非200状态（含202、重定向、408及5xx）
均视为结果未知，保留预留并停止回退。本机TCP故障传输与真实DB已验证该控制流，不代表真实模型回退成功。
OpenAI 固定官方 Responses URL、store=false、输出上限256；DeepSeek 固定官方
`https://api.deepseek.com/chat/completions`，使用非流式短请求和256 token上限；Ollama 仅运维命名 allowlist。
DeepSeek 与 OpenAI 一样受运维开关、租户加密凭证、管理员 consent、每日额度和本地转云显式偏好约束；
不会把 `.env` 中的运维密钥自动注入任一租户，也不接受用户自定义云端 URL。
不使用环境隐式代理、不重定向，不接受用户任意 URL。用量按真实响应，未知费用为空。
请求要求identity编码，压缩响应在读取前拒绝，原始响应累积前强制64KiB上限。
Ollama仅done=true且done_reason=stop成功；云/本地空白或截断结果按未知处理，保留预留并停止回退。
固定0008升级DeepSeek类型约束，0005保持冻结；finish_reason仅接受stop，截断/过滤/空内容停止回退。
模型配置最多5条；管理员可通过固定更新接口编辑原配置的模型、优先级、授权端点和密钥，供应商类型不可改。
未提交密钥保留原密文；专用停用动作清除密钥，云端重新启用必须补钥、明确同意且运维开关已开放。
更新只改配置，不发模型请求；历史usage保存调用时模型，配置ID及用量关联不变。0009追加provider.updated审计动作。
本机DeepSeek两次短请求已成功，Web BFF/加密配置/真实Token入库与清密钥已验证；费用保持未知。
普通运行不读取全局平台密钥注入租户；显式单次验收脚本只向自行创建的测试租户保存密钥并清理，
未知结果保留usage/测试空间、撤销会话并清密钥，不重试。OpenAI原凭证无效，Ollama仍待验收。
追加授权的一次有效deepseek-flash短请求已证明官方明确拒绝后成功回退，failed/succeeded两条usage与真实Token一致，
测试密钥/空间已清理、运维开关恢复false；不代表本地转云/Ollama推理或生产发布完成。

## 加固与恢复

API 普通 body16KiB/文件10MiB/接收30秒；来源检查和保存为17KiB JSON、16KiB UTF-8文本，BFF同限。
授权正文/字幕导入仅精确POST路径允许128KiB JSON，正文另限60,000字符/64KiB UTF-8；不会扩大其他入口。
BFF 模型请求16KiB，连接测试65秒、总结135秒（最多两次有界尝试）、普通15秒；网关响应140秒。
请求日志只记随机 ID/路由模板/状态/耗时，SQL参数隐藏，原始 access log关闭，异常正文不输出。
生产启动检查秘密/HTTPS来源/可信Host/Redis认证/无管理连接，未验证邮件前禁止生产注册。
公共/S3端口和Redis协议/DB路径/参数严格校验；rediss要求证书/主机名验证。
API lifespan及Celery Worker/Beat同步loader另检查真实current_user/session_user非super且无BYPASSRLS，
拒绝管理员SET ROLE伪装；普通导入/inspect不连接，启动探针结束释放池以免prefork继承连接。
docker-compose.hardened.yml 提供只读应用容器、tmpfs与CPU/内存/PID上限；已实际完整验收通过。
运行镜像固定官方manifest摘要，Web运行阶段无npm/yarn/corepack，Python运行阶段无pip/ensurepip；
构建/测试阶段保留工具。Web zlib精确安装1.4-r0；实际Image ID绑定的Scout SARIF和服务门禁分别记录，
扫描失败/容器漂移拒绝通过，保留历史测试镜像与未修复项。详见docs/runtime-image-security.md。
Nginx网关同样从固定官方基础构建，仅保留当前HTTP/TLS代理消费者，卸载未加载动态模块及无消费者XML/图像库；
精确安装zlib1.4-r0。运行非root只读，TLS1.2/1.3分别强制验收，无系统信任或公网修改。
加密恢复脚本同机新DB/private bucket，0017后已校验29表/RLS/授权/表与列权限/约束和对象，含人工修订及分类启停/操作者；不等于异地切换验收。
CHECK用PostgreSQL原生临时视图重解析，接受pg_dump等价cast转写，验证/延迟状态仍逐项比较。

## 业务审计

固定0006_audit建立append-only audit_events，0009追加provider.updated后记录13种固定业务动作、actor/resource UUID与时间。
成员、任务、文件和模型设置关键变更在相同数据库事务写审计；失败回滚不留下成功记录，
文件仅在 ready/实际删除提交时记录，幂等任务重复提交不重复记录。
不记录内容、提示词、模型密钥、文件名或任意 metadata。INSERT RLS绑定租户与实际 app.user_id，
复合外键绑定空间成员；运行角色只有SELECT/INSERT，不能更新/删除历史。
GET audit-events需有效成员和管理员身份，按(created_at,id)游标分页，每次最多50条。
账户页提供真实审计查询；部署审计导出/保留政策和独立防篡改归档仍待补齐。

账户安全历史由独立全局表auth_security_events承载，与空间无关；固定0010追加8种固定动作。
注册/登录/注销/全部撤销/邮箱验证/密码重置的成功记录与身份变更同事务提交。
失败登录观察先提交再返回401/403，按用户/动作/数据库15分钟窗口去重；未知账户合为匿名时间桶。
不存邮箱、IP、密码、令牌或自由文本，不提供逐次失败计数；限流前拒绝及失败恢复链接尚未覆盖。
FORCE RLS只允许用户读取自身，匿名桶不可读；运行角色仅SELECT/INSERT，无更新/删除权限。
API/BFF仅GET自身分页，账户页手动加载；注销或令牌切换清空视图，不依赖空间成员权限。
详见docs/account-security-history.md；归档/保留和外部告警仍为独立生产门禁。

本机审计归档CLI复用身份/成员/管理员校验，仅用应用角色在只读REPEATABLE READ事务导出
指定空间业务审计和当前账户历史；匿名桶/其他账户不导出。行数/时间/字节有界，AES-GCM加密，
当前Windows用户DPAPI保护密钥，离线先认证再解析；不删除源记录。
仅支持固定acceptance栈，不等于生产独立防篡改/保留策略完成，详见docs/audit-archive.md。

## 部署与演进

Compose 提供 postgres、redis、minio、migrate、storage-init、api、worker、beat、web。
命名卷持久化。所有暴露端口只绑定 127.0.0.1；不配置公网入口、TLS、付费调用或定时自动采集。
API/Worker 用非 root 容器账户。依赖安装在项目本地 venv/node_modules，不修改系统配置。
Docker 镜像与依赖应在生产前使用漏洞扫描、digest 固定及更新流程。
Python外部依赖按`requirements/runtime.lock`、`dev.lock`、`build.lock`强制SHA-256和二进制wheel安装；
原`requirements.lock`保留版本约束来源。目标为CPython3.12 Windows amd64和Linux glibc>=2.28 amd64。
项目wheel使用固定构建后端、禁网且无隔离隐式下载的独立阶段；运行镜像不携带开发/构建工具。
离线锁生成器核验实际工件元数据、平台、markers/extras与闭包；重生成检查需要本机忽略的wheel缓存。
Windows全新安装/篡改拒绝与Linux无缓存构建已实测，不替代CVE/许可/签名或独立安全评审。
模型接入、采集、向量、图谱都按 TASKS.md 逐步实现，不提前添加无授权的外部操作。

来源候选消费者位于 modules/sources：成员授权、tenant→member锁后写入 source_batches/sources，
UUID 幂等、租户内 canonical SHA-256 去重与完整值复核、配额和 source.batch.created 审计同事务提交。
两表 FORCE RLS/复合外键、仅追加。有效原链接保留，危险拒绝链接只存占位与固定原因；无整段文本持久化。
账户收件箱检查/保存/列表均调用真实 API；公开采集和人工导入由独立任务消费者生成不可变快照。
0015的provenance把platform_collection与user_import分开；基本SRT/WebVTT时间标签保留，但不核验音视频对齐。
人工导入不访问关联URL，不依赖采集开关，不调用模型；后续笔记由服务端强制保留未核验范围，
详见docs/authorized-content-import.md。平台拒绝访问不以人工内容替代远端成功证据。

## 验收环境

MinIO 使用同版本官方 GitHub Release Linux amd64 文件构建本地非 root 镜像，不依赖已不可访问的
minio/minio 镜像。scripts/prepare_minio.py 固定版本/大小/SHA-256，构建时再次校验；文件仅在
.cache/minio（Git 忽略）缓存。底层 Python 镜像按 digest 固定，带 CA 与健康探针；不切换 S3 实现。
该构建当前仅覆盖 Linux amd64，不代表生产漏洞扫描、签名供应链或长期版本支持已完成。

docker-compose.acceptance.yml 使用 Compose !override 替换端口，独立 project 名避免复用主开发卷。
测试镜像用多阶段 test target 安装测试依赖，runtime target 不携带 dev 工具。
acceptance profile 使用测试环境管理连接，仅在独立验收容器中运行；运行服务仍只持应用连接。
私有对象读写测试验证匿名拒绝，本身不能替代用户级下载授权验收。重启探针只写专属 schema/key，
验证后清理自己的标记，不删除表、bucket 或卷。所有实时验收结果必须真实执行后才能记录为通过。
