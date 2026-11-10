# 本地多语言向量检索

固定模型与工件清单：`infrastructure/embedding/model.json`。CPU ONNX、384维、attention
mask mean pooling、L2归一化；模型和tokenizer启动逐一检查SHA-256。下载命令只取得公开模型，
推理服务无下载器，独立内部网络无公网出口，不调用付费模型。

```powershell
./.venv/Scripts/python.exe -m scripts.prepare_embedding --download
# 启动时叠加 docker-compose.embedding.yml，保留原有受限S3/加固覆盖文件。
```

空间管理员在固定修订详情展开“建立本地语义检索索引”，明确同意后索引标题与Markdown。
每段96字符、最多128段/12288字符；模型128 token限制可能继续截断，不能声称全文语义覆盖。
索引同版本同模型复用，无模型自动重试；整个修订原子提交，中断未提交可显式重试本地计算。
每空间最多20000段、UTC每日2560新增段；旧修订保留并占配额，没有自动清理。

0029 `semantic_chunks`直接使用pgvector，复合FK绑定租户/笔记/快照/修订，FORCE RLS，
有效成员只读、管理员追加，数据库检查片段等于指定修订对应位置，禁止更新/删除。
服务端无法仅凭向量证明模型确实被执行，模型边界和真实推理集成独立验证。
外部CPU推理不占DB事务；每批前与提交时重新核验身份/成员/角色。审计和全部向量同事务提交。

`POST knowledge/semantic/index`只接收note_id、revision_id、consent；
`GET knowledge/semantic/search?q=...&limit=...`只有1至96字查询和1至20条上限。
租户取认证路由成员关系，不接收请求体或header覆盖。索引与查询均独立Redis限流。
查询前后核验授权，不允许任意嵌入URL或跨云回退；失效/离线返回明确503，UI清空旧结果。

查询只联查最新修订和当前模型，精确余弦检索/同篇最高段/0.35阈值/最多20篇；索引扫描
有5秒语句上限，不使用ANN，租户数据不因全局近似候选被隐性漏掉。响应含最新笔记数、
已索引最新笔记数、超过字符上限的笔记数。相似度不是概率、事实、支持或因果关系。
每条返回确切修订和原片段；旧版本不再进入查询，未索引新修订需要重新建立。
字面检索、研究问答仍使用原接口；尚未接入向量RAG或混合重排、自动索引Worker。

维护检查：`pytest tests/test_semantic_search.py -q`；真实服务检查使用独立测试库和
`RUN_STACK_INTEGRATION=1`，叠加`docker-compose.embedding-acceptance.yml`运行
`tests/test_semantic_search_integration.py`。真实CPU/pgvector结果以PROGRESS记录为准，
无服务不以假向量计集成通过。浏览器需真实本地服务与隔离协议账户，禁止对用户笔记试写。

维护实际入口：`python -m scripts.semantic_acceptance --acceptance`（真实服务3项）；
追加`--browser`验证Chrome、手机、断网清空和自身清理。全栈使用
`./scripts/acceptance.ps1 -Hardened -RestartCheck -Embedding`，不带Embedding时
向量集成明确skipped且全栈门禁拒绝将跳过记为通过；不是用mock替代。
恢复使用`python -m scripts.foundation_recovery --acceptance`，源启用嵌入时自动生成
自身真实向量夹具并比较源/目标向量文本哈希、模型和所有复合关联，目标不联网下载模型。

依赖单独锁在`infrastructure/embedding/requirements.lock`，Windows/Linux amd64
工件32个SHA-256已对照官方PyPI；新增模型镜像仍需独立漏洞、许可证与生产资源评审。
未增加系统工具或连接用户设备。恢复包含新增表/向量，固定模型由工件清单另行准备。
