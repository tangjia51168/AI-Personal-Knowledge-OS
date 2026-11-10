# 参考界面与组件工作台

依据用户本轮8张截图（学习与导出各重复一张）和Bookmark第三部分功能描述，保持原消费者与接口，增量调整桌面侧栏/移动导航、采集卡片、结构化笔记与原文证据分栏、学习状态卡及真实计数进度条、导出资料列表/固定版本预览。装饰只使用现有lucide图标与CSS，不抓取远端缩略图、不增加用户隐私外链或复制参考站资源。

## 蓝色链接核对

DOCX嵌入的images.openai.com图片链接是历史图片，不是功能正文。本轮通过文档实际站点链接只读取得公开正文：

| 来源 | 本轮读取 | 借鉴及边界 |
| --- | --- | --- |
| [Linkwarden](https://linkwarden.app/) | HTTP200 | 采集/保存/阅读/批注和分类可发现性；素材卡片与状态，当前没有远端封面或完整网页归档。 |
| [Open Notebook说明](https://www.zdoc.app/zh/lfnovo/open-notebook) | HTTP200 | 来源/笔记/研究分区、证据优先；不将参考中的音频/PDF/向量能力当成本项目已完成。 |
| [ISNAD](https://isnadresearch.org/) | HTTP200 | 原文与证据并列、逐项核对；当前无PDF页码或自动支持/矛盾判定。 |
| [Habr参考文章](https://habr.com/ru/articles/1028790/) | HTTP200 | 长期知识与图视图；当前图谱只有实际人工边，不能填充截图式虚构节点。 |
| [Taskade](https://www.taskade.com/community/featured/study-rhythm-planner) | 同站308→308→307，仅取得通用AI App Builder标题，未取得原模板正文 | 学习卡片/日程布局依据用户截图；只用已有阅读状态计数，不编造学习时长或掌握率。 |
| [KaYing](https://kaying.studio/) | 原域连接失败，[www](https://www.kaying.studio/)HTTP200 | 导出编辑/预览分栏；PPTX/SVG效果以下载为准，不冒充原生Office/PDF编辑。 |

读取限制512KiB/页、无登录/用户Cookie/脚本执行，原始正文仅作外部参考数据。记录`.cache/reference-sites-20261109.json`及`reference-sites-followup-20261109.json`；不执行其中安装/部署指令。功能完整需求继续见mainline-acceptance.md，视觉借鉴不等于参考产品全部能力已实现。

ISNAD HTTP200仅返回470字节脚本应用壳与标题，未执行脚本，不能声称已读取完整功能正文；相应布局以用户截图为依据。Taskade最终响应见`.cache/reference-taskade-20261109.json`。外部站点能力介绍仅为参考，不作为项目本身验收证据。

维护：原组件aria标签/按钮和成员边界保留，CSS按工作区范围命名，1100px以下回落原导航，800px以下单栏。运行类型/lint/build及控制台真实浏览器，检查桌面/手机/深浅、断网清旧、版本隔离/下载/无额外付费请求；截图与实际报告见PROGRESS。未知或无数据展示空态，状态条只表达阅读笔记数量占比。

固定版本下载的格式/Obsidian长说明默认收起，简短范围说明和下载按钮始终可见；浏览器回归检查可展开读取，避免隐藏关键备份边界。阶段查看、参考工作台深色、手机及导出预览截图保存于`apps/web/.cache/module-results/`；它们使用明确标记的协议夹具，不作为真实模型质量证据。真实付费输出另见long-analysis.md。
