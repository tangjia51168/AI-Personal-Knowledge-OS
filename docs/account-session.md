# 统一会话与组件工作区

浏览器一次登录后可访问所有已实现组件，刷新、同源新标签页和页面跳转共用会话。后端原有绝对有效期为12小时，不因读取、刷新或活跃使用续期。退出会删除服务端会话；全部撤销、密码重设和实际过期仍由API强制核验。

## 接口与维护边界

- FastAPI继续只接收Bearer，每次验证身份、有效成员和租户上下文。GET `/auth/me`新增服务端`expires_at`，不返回凭证；数据库与冻结0027迁移不变。
- Web BFF将登录/免验证注册的Bearer截留，写入`knowledge_session` HttpOnly/SameSite=Strict、无Domain、Path=/api/account、绝对Expires Cookie；JSON删除token。HTTPS及固定本机TLS网关使用Secure，本机loopback HTTP开发入口不使用Secure。禁止把当前HTTP开发配置当生产配置。
- BFF有Cookie的写请求和登录/注册必须提供`X-Knowledge-Request: 1`；Origin存在时必须与Host精确同源，拒绝cross-site Fetch Metadata。无Origin的非浏览器客户仍必须提供标记；不设置跨域CORS。网关保留原Host并固定转发HTTPS协议。API直连Bearer保持原有客户端协议。
- 所有账户消费者使用`accountFetch`，不向页面暴露Cookie值。现有组件token参数只是内存挂载标识，发送前去除Authorization；凭证不进localStorage、sessionStorage、URL、HTML或BroadcastChannel。
- 页面加载/焦点恢复/每分钟核验真实会话，绝对到期定时清空消费者；任一401清空工作区。BroadcastChannel仅发送变更信号，其他标签页清空后独立核验；没有此API时依靠焦点/周期核验。网络失败不冒充注销或登录成功，服务端仍拒绝已撤销写入。
- 图标导航保留固定片段白名单和邮件令牌分流；只显示当前组件，隐藏组件保留草稿。换账号、退出或空间变化卸载相关消费者；刷新不会保存未提交草稿。普通成员能发现所有入口，管理员能力仍按后端角色限制。

## 检查

`./scripts/check.ps1`包含会话安全边界Node回归、Python、类型、lint和生产构建。`./scripts/acceptance.ps1 -Hardened -RestartCheck`只在独立验收库运行真实会话过期/注销/成员撤权检查；`scripts/track_acceptance.py --track console --run browser`验证HttpOnly/绝对期限不续期、刷新、新标签页、跨站拒绝、多标签页退出、远程撤销401及导入阅读导出。组织主线浏览器检查覆盖原目录、证据、图、批注和保存同键重试。禁止输出Cookie或打开含凭证trace。

当前没有外部身份供应商SSO、静默续期、跨域第三方客户端会话或生产上线。长文全文理解、向量检索与通知自测属于后续独立功能，不通过本会话模块宣称完成。

本轮160真实集成/重启/受限S3、702非集成/37Node及静态构建，Chrome控制台12/12、组织16/16、其余组件15/15通过；最后统一检查和成果看板以PROGRESS顶部为准。本机TLS额外验证Secure/HttpOnly清除Cookie、跨站登录拒绝。未新增付费请求或schema，未重跑无变化的全库恢复。
