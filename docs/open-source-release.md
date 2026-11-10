# 开源源码快照

源码发布与托管应用部署分开。当前公开预览仍有生产漏洞、平台/语义质量、外部邮件及真正异地验收缺口，不因此标记整个项目完成。

`python -m scripts.source_release`从固定源码目录和文档清单准备独立目录及ZIP，不含私人需求附件/对话、秘密、运行数据、模型或第三方二进制。保留源码布局和锁，公开README/任务/规格替代私人历史，原创源码Apache-2.0；第三方服务保留自己的许可。所有文本统一LF，SOURCE_MANIFEST.json逐文件SHA-256；`--verify <source>`检查未篡改、无额外文件、路径/大小/符号链接与有限敏感内容规则。此扫描不能代替人工审查和独立安全评审。

公开前执行完整静态/真实集成、Chrome和新schema恢复，并从独立目录构建Web/API确认配置未遗漏。原仓库不直接git add或推送。GitHub工具通过Windows凭据管理器取得已授权凭据，仅在内存经curl stdin传递，保持TLS校验；固定账号与ID复验。创建前拒绝同名既有仓库，不强推、不改变全局Git配置、不写令牌；safe.directory仅给本次已验证快照的单条命令。输出仅仓库URL与提交ID，推后远端main与本地提交比对。

入口：`python -m scripts.github_publish --source <source> --publish-authorized`。必须已有明确创建公开仓库授权和设备登录；失败后保留现场并核对远端状态，不能盲目重试或覆盖。维护回归：tests/test_source_release.py、tests/test_github_publish.py；完整检查scripts/check.ps1。
