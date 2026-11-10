# 人工概念卡片

知识组织主线的阶段范围，来源为Bookmark段落1308–1322及1336–1337。人工名称/定义与一处原文证据作为不可变记录保存；同名卡片可分别表达不同解释。功能真实验收已通过，主线仍部分实现。

`GET|POST /spaces/{tenant}/notes/{note}/revisions/{revision}/concepts`不接受查询参数。POST严格字段为idempotency_key、name、definition、evidence_key、expected_quote。名称复用NFKC/60字符/240字节，定义1–2000字符/8000字节，证据必须属于精确修订及快照、原文逐字一致。响应包含固定note/revision/snapshot、人工作者/时间、原文、method=manual、confidence=null；不推导可信度或事实核验。

0024只追加knowledge_concepts及证据复合唯一约束；租户/笔记/快照/修订与证据四元组均有复合FK，作者须同空间成员。FORCE RLS、仅SELECT/INSERT，无应用UPDATE/DELETE。应用授权租户写锁后复验身份/成员，实际记录与organization.concept_created审计同事务提交；同键同请求返回原结果，改变内容/修订/操作者409。每修订20张、空间1000张；幂等重放先于配额，读取超过界限报错不静默截断。

页面入口：账户→知识笔记→打开笔记→人工概念卡片→读取本修订概念与证据→填写并确认→保存→重新读取核对。草稿锁住版本/列表导航；改参数取消确认，卸载取消请求；提交未知保留原内容/幂等键，清空旧列表/证据，手动同键重试或读取核对。身份/空间/修订切换重建组件。

维护入口：`./scripts/check.ps1`覆盖Python输入/Node响应契约、lint/类型/构建；`./scripts/acceptance.ps1 -Hardened -RestartCheck`包含真实概念集成，`scripts/track_acceptance.py --track organization --run browser`包含页面回归。新schema须另作`scripts/backup_recovery.py --acceptance`的新目标加密恢复；只使用隔离验收栈和独立测试数据。固定入口及生产/外部边界见maintenance.md。

限制：只追加单证据卡片，不编辑/删除/合并，不跨修订自动迁移；无统一全库概念目录、方向关系、自动抽取或向量检索。导出暂不包含概念卡片；完整空间包仍需覆盖它，运维DB备份不能充当用户便携包。无模型/平台请求、设备/vault访问；历史暂缓及生产门禁保留。

验收：661非集成/31Node、静态/生产构建；`.cache/acceptance-concept-cards-verified-20261109.log`147/147真实集成/重启/受限S3，0失败/错误/跳过；首轮146/147旧分类登录限流证据保留，减少本模块多余登录后完整通过，不清桶或改产品限流。主线Chrome`.cache/track-acceptance/c2d4532af5f44facbe4684d63b0b169c`16/16（36.5秒）含真实概念流程与手机；其后补表单布局，最终展示复验见PROGRESS。

0024已应用冻结。新库33表/1对象加密恢复`.cache/recovery-drills/91351159350a4fbca5a4257267e9c3c7/summary.json`通过，恢复前特建实际概念记录，验证名称/定义及精确修订/快照/证据关系；全库数据/约束/RLS/权限/触发器指纹一致，服务恢复健康，自身夹具已清理，源数据与新恢复目标保留。旧0023的32表证据不代替本次恢复；同机演练不等于生产异地灾备。

最终表单样式与第30项成果记录在`.cache/check-concept-cards-final-20261109.log`统一661非集成/31Node及全部静态/生产构建通过，最终Web健康。完整主线Chrome报告`.cache/track-acceptance/930e3789acf24456a2cbd3ac409e542f`16/16（39.6秒，0失败/跳过/flaky），桌面/手机及成果卡截图已目视核对，临时Web/自身账户已清理。本机TLS报告ab29d80cba0945ba9ead143920695f0b、只读运维02358e7780794f85b4cd905d825c69d8通过，非生产TLS或镜像漏洞复扫。成果入口`http://127.0.0.1:13000/progress#module-concept-cards`。
