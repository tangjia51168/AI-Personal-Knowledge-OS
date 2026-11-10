# 基础证据问答维护

入口：账户研究区。无模型knowledge/research只列固定修订候选；POST /spaces/{id}/knowledge/answers才是有幂等历史的字面检索基础RAG。GET集合返回当前成员自己的最近20条，GET单项仅本人可读；管理员没有跨用户问答读取特权。BFF允许精确方法/路径，拒绝查询覆盖参数。

## 数据与授权契约

0026追加research_runs/research_citations，两表FORCE RLS。run绑定当前用户/空间/幂等键/请求摘要，pending只可转succeeded/failed/unknown，输入不可改，终态不可再更新；引用绑定tenant/run及tenant/note/snapshot/revision和精确原文证据四元组。RLS核对原引文、标题、版本和来源；应用角色不可修改/删除引用。

复用authorized_transaction，先身份/成员验证，锁后复验；begin/finish要求owner/admin，读取只要求仍有效的本人。每日每空间20条，同一用户pending/unknown阻止新收费请求。同键先回放，键/输入或操作者不符409，未知结果只能查询或原键核对，不自动换键重试。

检索最新修订最多8篇，每篇前2引文、最终最多8段，固定旧版本保存。引用R001–R008。问题1000码点、检索120码点，沿用纯文本/URL/控制字符和总上下文边界。来源/问题是不可信数据，模型无工具。输出严格JSON、引用必须在检索集合，最多6陈述；引文定位不证明事实或逻辑蕴含。

没有证据直接succeeded/insufficient_evidence，ai_usage零增长；有证据才按优先级选择首个已授权DeepSeek，一次请求，无自动云回退。调用前预留额度，实际用量先保存再校验回答；未知结果保留不确定性。审计research.created/research.finished与业务原子提交。

## 故障与局限

Web保留原键、网络失败锁住输入，历史可重登录后找回，读取不调用模型，失败清旧列表。最多20条，尚无分页/删除/分享。权限中途失效或进程异常可留pending，阻止重复收费；没有自动超时改失败或人工结算消费者。操作员须核对供应商用量和原请求，通过审查后维护，不直接改终态或自动释放额度。

本轮无新付费请求；真实DB/检索/RLS/无证据/无Provider与浏览器检查不代表新问答生成质量或外部成功。旧DeepSeek总结证据不替代本功能推理验收。向量/混合检索、范围筛选、共识/冲突研究待实现。

固定回归：check含research_content/Node解析/BFF契约；track_acceptance.py --track rag --run integration或browser；完整acceptance.ps1。真实DB覆盖并发幂等/原文伪造/终态不可变/同空间他人隔离/撤权/原子审计/零模型用量。新schema恢复读取实际人工笔记和failed但有真实引用的问答。
