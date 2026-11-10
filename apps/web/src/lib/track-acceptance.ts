// Versioned requirements, independent of runtime tenant data and test totals.
// "recorded" refers only to the explicitly named historical scope, never the whole track.
export type RequirementState = "recorded" | "partial" | "planned" | "deferred";
export const requirementStateNames: Record<RequirementState, string> = {
  recorded: "已有范围有验收记录", partial: "部分实现，仍有缺口", planned: "待实现及验收", deferred: "暂缓，未验收",
};
export type TrackRequirement = {
  id: string; title: string; state: RequirementState; acceptance: string;
  gap: string; paragraphs: [number, number]; modules: string[];
};
export type TrackAcceptance = {
  track: string; goal: string; workflow: string; dependencies: string[];
  document: string; requirements: TrackRequirement[];
};
export const referenceRecord = {
  document: "Bookmark Organization Plan.docx", paragraphs: 1992,
  sha256: "0221c3296ef4bc4477caf8f86e9c5f927172f2ea05c6dc953b26f93ab2bba8fe",
  status: "Bookmark 文档已作为两条新增对话的需求来源读取；链接正文未另行取得，历史启动指令仅作需求参考。",
};

// This run's scope checks. Counts may overlap between tracks; never sum into progress.
export const trackVerification: Record<string, { integration: string; browser: string; remaining: string }> = {
  foundation: { integration: "全栈160/160通过，零失败/错误/跳过；38表/2对象16项跨实例/新业务/冷队列恢复通过", browser: "控制台Chrome12/12含桌面/手机/主题/成员及最新流程，零失败/跳过/flaky", remaining: "同机跨实例不替代异地；生产切换、外部告警及完整容量未验收" },
  sources: { integration: "23项通过，0失败/错误/跳过", browser: "主线集合覆盖筛选/分页/导入页面边界", remaining: "不代表各平台真实采集通过" },
  understanding: { integration: "本轮18/18真实集成；改后长文3次付费、27/27片段、质量门禁及检查点通过", browser: "分段/尾部引用/手机/导出预览纳入控制台集合，最终报告见PROGRESS", remaining: "成功阶段可继续、未知已发出阻重发；一般语义/单位/矛盾未完成，费用金额未知" },
  evidence: { integration: "22项通过，0失败/错误/跳过", browser: "修订/证据绑定与读取在主线集合中通过", remaining: "时间定位、完整度与事实核验未通过" },
  organization: { integration: "全栈165/165真实集成，含实际CPU向量、版本失效、撤权与审计回滚；40表恢复19项通过", browser: "组织16/16与新增向量2/2通过，零失败/跳过/flaky，覆盖手机与断网清空", remaining: "全库概念目录/方向关系/混合重排未完成；向量仅显式索引的最新修订" },
  notes: { integration: "全栈160项包含人工首篇/版本/导出/原子审计，零失败/错误/跳过", browser: "Chrome12项包含本机文件/人工首篇、JSON/单版本与批量ZIP、PPTX/图文/流程图下载", remaining: "完整空间包/导入和客户端仍未验收" },
  learning: { integration: "27项学习通知真实集成与最终169/169全栈通过；独立Beat/Worker、本机SMTP和43表回执恢复通过", browser: "学习通知Chrome4/4，订阅/自测/已读刷新/关闭/手机/失败清空通过", remaining: "外部邮箱送达需资源；持久题库/复习事件历史/自适应间隔未实现" },
  rag: { integration: "全栈160项含2项问答真实检索/幂等/引用伪造拒绝/原子审计/本人历史/撤权", browser: "Chrome12项包含无证据不调用模型、个人历史打开及普通成员隔离", remaining: "无新付费推理；向量/高级研究和未知结果结算未验收" },
  sync: { integration: "全栈160项含3项计划保存/并发/暂停/撤权、真实Broker与Worker禁用路径及变化快照", browser: "Chrome12项包含保存/暂停/恢复/断网清旧", remaining: "不代表新平台外部采集通过；无收藏夹账号或自动建议" },
  console: { integration: "统一限时会话后160真实集成通过，身份期限/撤销/成员边界不变；702非集成/37Node及静态构建通过", browser: "统一会话Chrome12/12与组织16/16，刷新/新标签页/退出同步/远程撤销/跨站拒绝、组件切换和手机通过", remaining: "本机V1基本范围已验；完整主线高级能力/自定义主题/PWA未齐" },
  "local-ai": { integration: "固定多语言ONNX CPU实际推理与pgvector3项通过，纳入165/165真实集成；长文真实费用单独记账", browser: "向量索引/复用/中文近义查询/手机与失败清空2/2通过", remaining: "本地生成/设备Agent/转云仍未验收；模型128token与单篇12288字符限制见维护契约" },
  release: { integration: "6项真实身份/运维建号/审计/RLS通过，零失败/错误/跳过；独立Compose/Nginx与禁网运行镜像预检通过", browser: "本轮未做生产环境验收；进度展示不代表公开发布", remaining: "配置包已验收；生产启动、漏洞和资源仍需独立门禁" },
};

export const trackAcceptance: TrackAcceptance[] = [
  { track: "foundation", goal: "为所有主线提供可隔离、可恢复、可维护的运行基础。", workflow: "启动 → 固定迁移 → 身份/RLS → 独立任务 → 私有对象 → 重启/恢复核验", dependencies: [], document: "ARCHITECTURE.md", requirements: [
    { id: "foundation-runtime", title: "固定架构与租户运行边界", state: "recorded", acceptance: "真实PG/Redis/S3及独立Worker可用；非bypass应用角色、成员撤权及租户复合关系检查通过。", gap: "记录仅覆盖本机已测范围；完整Phase 0仍未完成。", paragraphs: [1369, 1402], modules: ["identity", "jobs", "files"] },
    { id: "foundation-recovery", title: "固定迁移与数据/对象恢复", state: "partial", acceptance: "在新目标恢复数据、约束、RLS、对象哈希；故障前后结果可核对，不覆盖原数据。", gap: "独立PG/S3/API/Worker及冷Redis outbox、源回切已实测；真正异地、生产切换/密钥托管仍未验收，Redis原损坏卷保留。", paragraphs: [700, 718], modules: ["foundation-recovery", "files", "audit"] },
    { id: "foundation-maintenance", title: "维护与回归成为持续门禁", state: "partial", acceptance: "契约、固定测试入口、配置/依赖/许可证、恢复说明随功能变更更新；性能与可观测性有实际基线，缺服务记未验收。", gap: "已补本地有界指标/消费入口、锁/许可盘点和24次业务性能样本；全业务容量、分布式链路/外部告警、全部历史专项审计与许可批准仍未完成。", paragraphs: [730, 754], modules: ["foundation-recovery", "jobs", "audit"] },
  ] },
  { track: "sources", goal: "合法取得多平台内容并明确实际取得的范围。", workflow: "分享文本/文件 → 规范化/去重 → 能力判定 → 授权采集/导入 → 快照/缺失记录", dependencies: ["foundation"], document: "docs/authorized-content-import.md", requirements: [
    { id: "sources-ingest", title: "批量入口、元数据与多级去重", state: "partial", acceptance: "覆盖URL/短链/分享文本及TXT/CSV/JSON/MD；保留原始和规范URL、平台ID/指纹、作者/发布时间及导入结果。", gap: "已有本机UTF-8文件预览/授权文本导入和最新快照变化留档；CSV/JSON不是收藏数据库解析器，完整元数据/收藏增量未齐。", paragraphs: [128, 162], modules: ["v-one-workspace", "source-schedules", "imports", "filters", "pipeline"] },
    { id: "sources-platforms", title: "六平台能力与收藏夹/合集", state: "partial", acceptance: "B站/抖音/小红书/知乎/YouTube/网页逐能力声明；官方授权或用户导出，不上传Cookie；失败原因可见。", gap: "网页正文已有实测；视频初版元数据不代表全文；B站412、其他平台及合集/收藏夹未完成。", paragraphs: [1040, 1124], modules: ["pipeline", "imports"] },
    { id: "sources-media", title: "字幕、OCR/ASR、关键帧及评论", state: "planned", acceptance: "优先字幕、无字幕时显式回退；分段含原时间/位置；评论按可得时间/赞/互动采样，弹幕和正文分别保存。", gap: "SRT/VTT人工导入不能替代自动采集；媒体解析、评论和弹幕消费者未实现。", paragraphs: [163, 194], modules: [] },
    { id: "sources-extension", title: "主动授权的浏览器扩展", state: "planned", acceptance: "MV3扩展可主动收藏/导入当前可访问内容；短期令牌、失败重试/撤销与权限边界可验。", gap: "未实现扩展；不自动安装或连接用户浏览器账户。", paragraphs: [1109, 1124], modules: [] },
  ] },
  { track: "understanding", goal: "以原文为依据完成分段到全文的深度理解。", workflow: "原始快照 → 分段/章节 → 段分析 → 全文综合 → 完整性/证据校验 → 不可变版本", dependencies: ["sources", "evidence", "local-ai"], document: "docs/content-knowledge-pipeline.md", requirements: [
    { id: "understanding-analysis", title: "分段、章节与全文综合", state: "partial", acceptance: "覆盖概念/原理、步骤/方法、案例/工具、数字/单位、局限和延伸；保留中间产物与处理版本，不把前缀当全文。", gap: "有界全部片段分段/综合与成功修订内阶段产物已实现并真实付费验证；0028独立阶段/用量/本人历史与成功阶段安全继续已验，未知阻重发；综合压缩不保证语义完整。", paragraphs: [163, 212], modules: ["long-analysis", "pipeline", "structured-reader"] },
    { id: "understanding-attribution", title: "作者、评论、AI与个人理解分离", state: "partial", acceptance: "每类观点可识别，AI推断不冒充作者，评论补充/争议有独立来源，人工编辑不被覆盖。", gap: "人工版本/个人理解已有；评论交叉分析与细粒度推断归因未实现。", paragraphs: [581, 625], modules: ["edits", "annotations", "pipeline"] },
    { id: "understanding-quality", title: "遗漏、数字与冲突检查", state: "partial", acceptance: "用已知段落/数字/矛盾样本验证覆盖、重复、单位与遗漏报告，缺内容明确标未知，保存模型/prompt版本。", gap: "已补完整片段分配/引用校验、综合数字优先和尾部125毫秒条件样本；不等于自动遗漏/单位/矛盾核验或完整质量评估。新付费授权单独计量。", paragraphs: [195, 212], modules: ["long-analysis"] },
  ] },
  { track: "evidence", goal: "每项结论可定位到准确版本与实际原始证据。", workflow: "原始素材 → 不可变快照 → 修订绑定 → 引文/位置 → 质量与完整度说明", dependencies: ["foundation"], document: "docs/structured-note-reader.md", requirements: [
    { id: "evidence-binding", title: "来源、快照、修订与引文绑定", state: "recorded", acceptance: "原始/规范URL分离；引文来自同租户同快照；打开命中修订后证据保持原版本，空缺明确提示。", gap: "仅覆盖已取得文本与保存引文，不证明全文或事实真实。", paragraphs: [615, 625], modules: ["pipeline", "imports", "structured-reader"] },
    { id: "evidence-locations", title: "时间戳、文本位置与完整度", state: "partial", acceptance: "视频时间和文本位置可复现定位；作者/评论分开；数字/单位与未取得片段有可追溯的检查结果。", gap: "已有原文编号定位；视频播放定位、完整度计算和数字语义核验未完成。", paragraphs: [195, 212], modules: ["structured-reader", "pipeline"] },
    { id: "evidence-conversations", title: "参考对话全文核实", state: "partial", acceptance: "Bookmark 文档作为两条新增对话的需求来源，按段落映射需求并记录版本。", gap: "新两条对话内容已通过用户确认的Bookmark读取；更早分享全文仍未取得，已恢复补全目标。链接页面未另行读取。", paragraphs: [1976, 1991], modules: ["mainline-acceptance"] },
  ] },
  { track: "organization", goal: "从知识检索和组织走向有证据的概念与关系导航。", workflow: "分类维护 → 最新内容检索 → 固定修订/证据 → 人工关系导航 → 概念及混合检索", dependencies: ["foundation", "evidence", "notes"], document: "docs/knowledge-organization-track.md", requirements: [
    { id: "organization-literal", title: "分类、字面检索与固定修订", state: "recorded", acceptance: "标题/正文/证据/批注及来源/分类组合筛选、分页、归档保留、固定修订阅读；撤权/断网清旧通过。", gap: "不检索历史批注、不做语义排序；大库性能未验收。", paragraphs: [626, 646], modules: ["organization-workspace", "label-management", "labels", "filters"] },
    { id: "organization-graph", title: "人工图谱交互与边界", state: "recorded", acceptance: "空间/局部图可打开准确修订；空间图支持标题搜索、连接筛选、缩放/平移与键盘替代，截断和人工关系含义始终可见。", gap: "本轮空间图交互与真实Chrome13项通过；局部图保持原导航，未扩展同类交互。不等同全库或语义图谱。", paragraphs: [676, 699], modules: ["organization-workspace", "note-links", "local-graph"] },
    { id: "organization-concepts", title: "主题层级、概念与证据关系", state: "partial", acceptance: "主题层级无环；概念定义/提及绑定证据；支持/反驳/前置等关系有方向、来源、方法、可空置信度，版本可追溯。", gap: "人工主题层级与固定修订概念定义/原文证据卡片已实现；全库概念目录、方向证据关系及合并/相似/冲突建议未实现，无方向人工链接不能替代支持/因果。", paragraphs: [1298, 1337], modules: ["topic-hierarchy", "concept-cards"] },
    { id: "organization-hybrid", title: "关键词与向量混合检索", state: "partial", acceptance: "按标签/主题/作者/时间筛选，增量嵌入不重复计费；显式租户过滤、版本失效与排序可验。", gap: "本地多语言向量独立检索、固定修订复用与最新版本失效已验收；作者/时间筛选、混合重排和变更片段增量未实现。", paragraphs: [640, 649], modules: ["semantic-search"] },
  ] },
  { track: "notes", goal: "长期保存素材、生成版本与个人知识，并可迁出迁入。", workflow: "阅读/编辑/批注 → 历史版本 → 明确选择 → 导出 → 新空间导入/完整性核对", dependencies: ["foundation", "evidence"], document: "docs/note-export.md", requirements: [
    { id: "notes-versioning", title: "人工编辑、批注和稳定资产", state: "recorded", acceptance: "素材/AI/个人理解分离；新版本不覆盖旧版，冲突拒绝，批注绑定原段，稳定ID和原始Markdown保留。", gap: "个人理解不等于事实核验，历史批注不自动迁移。", paragraphs: [700, 709], modules: ["edits", "annotations", "structured-reader"] },
    { id: "notes-formats", title: "多格式导出与模板", state: "partial", acceptance: "固定修订的格式、证据/来源、大小与资源限制明确；补齐PDF/CSV/Mermaid、Notion及研究/课程/思维导图等模板。", gap: "现有八格式和批量ZIP有下载记录，不能代表所有格式、模板或第三方客户端兼容。", paragraphs: [257, 288], modules: ["note-export", "obsidian-export", "batch-visual-export"] },
    { id: "notes-portable", title: "便携完整空间包与双向导入", state: "planned", acceptance: "历史版本、分类/链接/批注、学习和附件有清单/哈希；新空间导入可复现，冲突/不可信包拒绝。", gap: "用户恢复实施目标，功能仍待完成验收；DB/S3运维备份和选中修订ZIP不能替代。", paragraphs: [626, 639], modules: [] },
    { id: "notes-clients", title: "Obsidian及演示客户端兼容", state: "planned", acceptance: "实际客户端打开、跳转/资源和演示可用，记录版本及局限。", gap: "用户恢复兼容目标，尚未实际验收；不自动安装客户端或访问个人vault。", paragraphs: [257, 288], modules: [] },
  ] },
  { track: "learning", goal: "从阅读记录发展为可解释的实践与复习流程。", workflow: "状态/清单 → 计划/日程 → 阅读证据 → 自测/实践 → 复习事件 → 提醒送达/分析", dependencies: ["notes", "evidence"], document: "docs/review-workbench.md", requirements: [
    { id: "learning-current", title: "个人阅读、计划与日程闭环", state: "recorded", acceptance: "成员独立四状态、筛选/总览/日程/ICS，按日预览确认后保存，复习工作台阅读并确认下一提醒。", gap: "现有完成时间仅最后一次保存，不代表次数历史或掌握率。", paragraphs: [650, 660], modules: ["learning-status", "learning-summary", "learning-review", "learning-schedule", "review-plan", "reminder-batch", "review-workbench", "learning-analysis"] },
    { id: "learning-history", title: "状态扩展、计划与复习历史", state: "planned", acceptance: "掌握/实践/归档与自定义状态语义明确；命名计划、事件历史、重复规则与自适应复习分别验收，不能按状态数推断掌握率。", gap: "未实现命名计划、次数历史、自定义状态或间隔算法。", paragraphs: [234, 256], modules: [] },
    { id: "learning-assessment", title: "自测、知识卡片与错题", state: "partial", acceptance: "题目和答案可回溯笔记版本与证据；用户确认自测结果，错题可复习，AI不伪造掌握结论。", gap: "固定修订无模型回忆卡、精确参考/原文、人工自评和本轮待练重练已验；仅页面内存，没有永久题库/错题历史或自动判分。", paragraphs: [234, 256], modules: ["evidence-recall"] },
    { id: "learning-notifications", title: "提醒送达与撤销", state: "partial", acceptance: "用户主动选择渠道；调度、幂等发送、失败/撤销和送达状态可核对，时区/重试可测。", gap: "主动订阅/站内收件箱、自测/已读/独立Beat-Worker、本机SMTP及未知阻重发已验收；外部收件箱无送达证据，无WebPush/短信渠道。", paragraphs: [1338, 1365], modules: ["notifications"] },
  ] },
  { track: "rag", goal: "仅依据授权知识回答问题和进行跨素材研究。", workflow: "授权范围 → 混合检索 → 证据上下文 → 回答 → 引用/版本核对 → 资料不足说明", dependencies: ["organization", "evidence", "local-ai"], document: "PROJECT_SPEC.md", requirements: [
    { id: "rag-grounding", title: "单素材与全库证据问答", state: "partial", acceptance: "租户/成员撤权实时生效；回答引用准确修订/位置；无检索结果答资料不足；检索内容不作为工具指令执行。", gap: "基础字面检索RAG/精确引用/本人历史及无证据零调用已验；没有新付费推理/向量/范围筛选，引用不保证事实与逻辑蕴含。", paragraphs: [289, 309], modules: ["research-answers"] },
    { id: "rag-research", title: "跨素材综合研究", state: "planned", acceptance: "按标签/主题/作者/时间限定，列共识/差异/矛盾，区分事实、推断、争议和未知；保留每项结论证据。", gap: "全文引用展示不能替代研究回答；等待检索和质量前置能力。", paragraphs: [640, 649], modules: [] },
  ] },
  { track: "sync", goal: "可控地增量处理收藏内容并保留各步骤产物。", workflow: "明确计划/手动触发 → 增量发现/去重 → 分步处理 → 建议确认 → 索引/归档 → 错误恢复", dependencies: ["sources", "organization", "foundation"], document: "ARCHITECTURE.md", requirements: [
    { id: "sync-jobs", title: "持久化任务与可恢复分步管线", state: "partial", acceptance: "每步状态/开始结束/错误/幂等/产物/断点可见，Worker重验身份与归属；未知外部结果停止自动重试。", gap: "已有outbox、独立Worker、取消/重试；完整分析/索引各步及断点产物未齐。", paragraphs: [700, 719], modules: ["jobs", "pipeline"] },
    { id: "sync-schedules", title: "收藏夹增量与调度", state: "partial", acceptance: "日/周/自定义及手动同步可暂停/撤销，增量幂等不重复产生素材和付费任务；撤权后停止。", gap: "已实现公开来源6小时至7天定时刷新/暂停/恢复/变化留档及撤权；没有收藏夹账号/新内容发现，本轮实际Worker禁用路径不冒充外部采集成功。", paragraphs: [661, 672], modules: ["source-schedules"] },
    { id: "sync-suggestions", title: "分类、相似与关系建议", state: "planned", acceptance: "建议有证据/模型版本和可解释来源，经确认写入，不覆盖人工分类/关系；缓存/增量更新有上限。", gap: "未实现自动分类、关系建议或自动归档消费者。", paragraphs: [730, 754], modules: [] },
  ] },
  { track: "console", goal: "让用户在实际页面完成各主线并看清结果与限制。", workflow: "总览/收件箱 → 任务 → 阅读/组织 → 学习/导出 → 设置/进度与成果", dependencies: ["foundation"], document: "docs/module-delivery.md", requirements: [
    { id: "console-workflows", title: "真实消费者和反馈", state: "partial", acceptance: "Dashboard、Inbox、任务/阅读/编辑/分类/图谱/学习/导出/问答/设置连通；加载/空/错/取消/重试及响应式均可测。", gap: "一版统一10组件、问答/导出中心/文件导入连续流程和成员边界已验；完整主线高级目标及PWA未实现，不用虚假业务统计填充。", paragraphs: [676, 699], modules: ["v-one-workspace", "research-answers", "source-schedules", "identity", "organization-workspace"] },
    { id: "console-themes", title: "主题与可访问交互", state: "partial", acceptance: "浅深/自定义主题与可扩展主题包；键盘可达、手机无溢出，图谱缩放/拖动/搜索/筛选有替代入口。", gap: "浅深主题已有；自定义主题包未完成，本轮补空间人工图交互。", paragraphs: [692, 699], modules: ["organization-workspace"] },
  ] },
  { track: "local-ai", goal: "在清晰授权、预算和隐私边界内选择模型。", workflow: "配置/授权 → 任务模型选择 → 额度预留 → 调用/明确回退 → 实际用量/未知结果记录", dependencies: ["foundation"], document: "ARCHITECTURE.md", requirements: [
    { id: "ai-provider", title: "Provider、加密密钥和回退", state: "partial", acceptance: "BYOK/管理员公共模型、任务级选择、优先级、健康与用量费用可核对；未知付费结果保留预留，不重复调用。", gap: "Provider/租户配置和部分云端实测已有；任务路由、公共模型/套餐及精确成本未齐，未知费用保持空。", paragraphs: [1003, 1034], modules: ["providers"] },
    { id: "ai-local", title: "本地模型与安全设备接入", state: "partial", acceptance: "自托管/本地Agent显式授权，服务注册/健康和本地转云隐私偏好可测；不把任意内网地址当共享端点。", gap: "固定CPU多语言嵌入与独立私有容器、实际索引检索已验收；Ollama生成/设备Agent与本地转云仍未完成。", paragraphs: [1034, 1039], modules: ["semantic-search"] },
    { id: "ai-efficiency", title: "缓存、任务模型与增量嵌入", state: "partial", acceptance: "内容哈希/模型/prompt版本驱动缓存，阶段产物持久化；只更新变更片段，成本上限和回退可追溯。", gap: "固定模型/修订向量复用与长文阶段持久化已验收；跨修订变更片段复用、完整分析缓存和任务成本上限尚未齐。", paragraphs: [730, 754], modules: ["semantic-search", "summary-checkpoints"] },
  ] },
  { track: "release", goal: "在实际安全、隐私和生产资源门禁通过后上线。", workflow: "账户/权限 → 用量/审计 → 数据管理 → 漏洞/资源检查 → 生产恢复/切换 → 发布", dependencies: ["foundation", "sources", "local-ai"], document: "docs/release-gates.md", requirements: [
    { id: "release-tenant", title: "身份、租户、存储与审计", state: "recorded", acceptance: "身份/成员复验、FORCE RLS、复合外键、服务端文件key、受限S3、原子审计和日志脱敏通过。", gap: "本机验收范围不能证明完整生产安全。", paragraphs: [1369, 1402], modules: ["identity", "files", "audit"] },
    { id: "release-privacy", title: "账户数据、保留与撤销", state: "partial", acceptance: "查看/导出/删除个人数据、保留策略、模型授权与密钥撤销可验；数据/附件清理规则明确。", gap: "模型撤销与部分隐私设置已有；完整个人空间迁移和账户数据删除未完成。", paragraphs: [1826, 1845], modules: ["providers", "identity"] },
    { id: "release-saas", title: "团队、RBAC、套餐与登录扩展", state: "planned", acceptance: "团队权限、公共模型/配额/付费、OAuth/MFA、带scope的开放API密钥按独立能力验收；不以预留结构代表可用产品。", gap: "既有成员角色不等于完整团队或订阅产品；外部API、主题市场与插件生态仍待实现。", paragraphs: [1777, 1825], modules: [] },
    { id: "release-production", title: "生产漏洞、恢复与发布", state: "partial", acceptance: "当前镜像漏洞门禁、生产域名/TLS/邮件、独立归档/异地恢复和切换演练均有实际证据。", gap: "独立部署配置包/输入检查/禁网预检/运维建号已验收；当前漏洞门禁未通过，公网/外部邮件/真正异地无资源。", paragraphs: [730, 754], modules: ["deployable-release"] },
  ] },
];
