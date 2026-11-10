# 人工主题层级

本功能属于知识组织与检索主线，复用organization目录及标签维护。0023迁移、全栈141项真实集成/重启/受限S3与同机加密新目标恢复通过；649非集成/29Node及静态/构建、完整主线Chrome14/14通过，0失败/跳过/flaky，桌面/手机已核对。

`GET labels/manage`增加`parent_id: UUID|null`。`PATCH labels/{id}`沿用必填`expected_version`，省略parent_id保留旧上级，显式null移回根目录；非空值只能是同空间启用主题。标签传parent_id（含null）拒绝422。创建接口保持兼容，先创建根主题，再明确移动；旧笔记分类响应不变。

自挂/挂后代返回409 `TOPIC_PARENT_CYCLE`；外空间/标签上级404 `TOPIC_PARENT_NOT_FOUND`；归档新上级409 `TOPIC_PARENT_ARCHIVED`。旧版本409，无变化不增加版本或审计。上级变更与名称/状态共享版本和一次原子审计，失败回滚全部修改。

归档保留已有子主题和笔记关联，不级联；原样保留归档父仍可重命名子主题，但不能新挂归档父。空间含归档最多100项，同类型名称全空间唯一；层级不改变笔记关联、修订、精确label筛选，不隐含后代筛选、概念或语义关系。

0023固定迁移增加parent列、同租户同类型复合FK/check/index和最小列权限。SECURITY INVOKER触发器保持RLS，显式租户条件，租户写锁及祖先共享行锁、100层有界检测防循环；应用统一先锁租户再写，锁后复验会话/成员。绕过应用的并发直写可能死锁/序列化失败，必须整事务重试，不能忽略错误。迁移不提供破坏性自动downgrade。

前端完整路径明确归档祖先，候选排除自身/后代/归档。保存需确认，改变参数取消确认；网络结果未知清旧目录，手动刷新核对，不自动重放。空间/身份切换沿用卸载取消。

固定维护：`./scripts/check.ps1`；真实服务`./scripts/acceptance.ps1 -Hardened -RestartCheck`；主线定向`scripts/track_acceptance.py --track organization --run integration|browser`。新schema须另作`scripts/backup_recovery.py --acceptance`同机新目标加密恢复，比较触发器定义/启用状态和调用函数定义/权限/执行配置。所有命令仅隔离验收栈，外部/生产/用户vault暂缓项保留。

恢复报告`.cache/recovery-drills/11386e0fdfaf468183547abf2dcd025f/summary.json`；隔离全栈报告`.cache/acceptance-reports/summary.json`，原数据与卷保留。SECURITY INVOKER函数/触发器禁用及提权漂移的检测回归已在真实PG通过。恢复是同机隔离演练，非生产异地恢复或便携空间包。

浏览器固定入口为分类与层级分配两个同时存活的临时只读Web（同一已构建Image ID、无卷、随机loopback端口和私网IP），只切换层级用例的origin，JSON汇总仍覆盖完整固定集合。保留历史来源IP记录避免重复使用尚在15分钟限流窗口的地址；不删除Redis桶或放宽产品限制，两个容器及自身账户均清理。

完整Chrome报告`.cache/track-acceptance/5eec809fa0af4e2d93087863af34defb`，36.9秒14/14；初轮共享IP429和disabled option断言失败保留报告，不算通过。主线整体仍partial，层级不替代概念/证据方向关系/向量排名。

最终成果展示另验：`.cache/check-topic-hierarchy-records-final-20261109.log`的9项Node/TS/lint/生产构建通过；`.cache/browser-topic-progress-final-20261109.log` Chrome3/3（10.7秒，0失败/跳过/flaky），12主线39项及第29项成果/入口/手机通过，桌面/手机截图已核对。最终本机TLS报告`34c061a5129748359fbc989487c2da13`和只读运维`41d7ad5e197b4c8589f54113afc739dc`通过，分别位于`.cache/gateway-reports`与`.cache/operations`；不是生产TLS或新镜像漏洞扫描。成果入口`http://127.0.0.1:13000/progress#module-topic-hierarchy`。
