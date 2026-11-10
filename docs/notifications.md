# 个人提醒与通知维护契约

默认关闭。成员主动保存订阅后，Beat每30秒发现最多50个UUID时钟索引；独立Worker重新验证用户、空间、成员、个人订阅及所属关系，每轮最多处理100条到期记录和20次邮件尝试。每个笔记/成员/到期时间只创建一次；深读记录除外。恢复订阅会发现仍到期且尚未通知的记录，不重复历史。5000条个人持久收件箱、每天3次自测，最近50条可查看；不自动删除历史。

0030三表：个人订阅、无正文/地址的全局UUID调度索引、个人通知回执。业务表FORCE RLS/本人+成员策略与复合FK；应用角色无删除、无身份/内容重写权限。订阅版本、创建/已读/投递审计与业务同事务。API/BFF严格方法/参数，无任意收件人/SMTP/模板输入。前端主动读取，错误清空旧数据，复用统一限时登录，不发送模型请求。

邮件另行启用，要求本人已验证邮箱及服务器SMTP配置。复用账户邮件安全传输；固定纯文本模板只包含工作区入口，不包含笔记正文或令牌。pending提交后可取消；发送前复验订阅版本/成员/用户/到期状态，dispatched持久提交后才进行SMTP且不持DB锁。此授权点之后的撤销无法撤回网络字节。

状态：not_requested、pending、dispatched、smtp_accepted、unknown、cancelled。SMTP接受不证明外部收件箱送达。超时/失败/发送后崩溃均不自动重发；超过两分钟遗留dispatched标unknown（订阅关闭/撤权时可保持dispatched，仍不重发）。系统不能在SMTP和数据库间提供exactly-once。自测请求幂等键避免同次重复；新自测为明确的新请求。无弹窗/WebPush/SMS/外部送达webhook。

检查入口：`.venv/Scripts/python.exe -m pytest -q tests/test_notifications.py tests/test_mail_transport.py`；`node --experimental-strip-types --test scripts/notifications.test.mjs`；真实隔离数据库与本机SMTP：`python -m scripts.track_acceptance --track learning --run integration`；浏览器同入口`--run browser`。全栈含向量须`./scripts/acceptance.ps1 -Hardened -RestartCheck -Embedding`。实际验收结果以PROGRESS为准，单元/本机SMTP不替代外部邮箱验收。生产配置参见docs/deployment-preparation.md，现无生产资源。
