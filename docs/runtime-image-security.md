# 本机运行镜像漏洞验收

镜像构建、应用回归和漏洞匹配是三项不同检查。服务健康或依赖审计为零，不能代替运行镜像的 CVE 验收。
最新结果与证据路径见 PROGRESS.md；本文件说明可重复的操作与判断方式。

## 当前修复

- Python/MinIO 基础固定为官方 Python 3.12 slim manifest
  `sha256:4840d514c583a4d3d8cad3940e68dee6f0f6f3635c12fcd0a3d74c21a5b93a54`。
- Web 构建和运行基础固定为官方 Node 24 Alpine manifest
  `sha256:a34ab6d7f6f4813f4d0c5469d74685604b962fbdbf5793a7bc7b4998ecb867c8`。
- 使用 Docker 官方 `registry.hub.docker.com/library/` 端点获取同一固定摘要；没有第三方镜像或 TLS 绕过。
  2026-11-07 官方标签元数据与实际拉取摘要均已核对。
- Web 运行阶段从 Alpine 官方签名仓库精确安装 `zlib=1.4-r0`。不会使用浮动 `apk upgrade`。
  如果仓库不再提供此版本，构建应失败并重新评审固定版本。
- Web 构建阶段保留 npm；运行阶段删除 npm/npx/corepack/yarn 及其全局依赖。
  Next standalone 使用 Node 启动，无运行期包安装消费者。
- Python 构建/测试阶段保留 pip；API/Worker/Beat/迁移运行阶段在安装及 `pip check` 后删除 pip 和 ensurepip。
  MinIO 同样删除其健康检查解释器中的 pip/ensurepip，Python 与 CA 保留。
  这不是删除包清单来隐藏漏洞：对应实际工具、代码及 ensurepip 内置 wheel 均移除。

所有删除仅发生在镜像构建的固定目录内，不修改宿主机 `.venv`、Node 安装或系统工具。
MinIO 官方二进制仍为原固定 Release 和 SHA-256，没有进行存储格式升级；其 Go 依赖漏洞仍须单独处理。
pgvector与Redis未升级，不把上游尚无修复或未复验的漏洞标记为解决。
后续Nginx网关已从同一官方程序版本派生精简镜像，详见下节及PROGRESS.md最新记录。

## 真实验收

先按 `scripts/acceptance.ps1 -Hardened -RestartCheck` 重建并验收隔离栈，再检查实际运行镜像。
命令只能使用模块方式：

```powershell
.venv/Scripts/python.exe -m scripts.scan_runtime_images --acceptance --allow-metadata-egress
```

`--allow-metadata-egress` 必须对应已经获得的用户授权：Docker Scout 向 Docker 官方漏洞匹配服务发送
PURL 与镜像层 digest。不会上传完整镜像、代码、密钥或业务数据。没有授权时不要使用该开关。
脚本要求两个显式开关，只接受固定 `knowledge-os-acceptance` 项目，不自动拉取或推送镜像。

每次输出独立 `.cache/scout-reports/<UTC时间-UUID>/`：

- 服务与容器到实际 Image ID 的映射，运行状态及可用的 RepoDigests。
- 按 Image ID 去重后的完整 SARIF、扫描时间、扫描器版本、报告 SHA-256、级别计数与修复版本。
- 运行镜像与全部盘点镜像分别计算门禁；已退出的旧失败测试容器保留现场，不冒充当前运行服务。
- 开始与结束的容器清单一致性。扫描失败、结果缺失或无效、运行服务缺失、扫描中镜像变更均不得通过。

SARIF 中 error/warning 任一存在即漏洞门禁失败，note/none 同样保留。不会忽略基础镜像、无修复版本、
低 EPSS 或被人工抑制的结果。退出码 0 表示所盘点镜像门禁通过，2 表示已扫描且含漏洞，1 表示扫描不完整。

原 20261107 报告没有完整 Image ID 绑定，不能继续当作重建镜像的有效验收证据。
新报告也只证明其记录的本机镜像与扫描时间，不代表生产镜像、供应链签名、许可或独立安全评审通过。

## 2026-11-07 实际结果

完整报告：`.cache/scout-reports/20261107T083453Z-a3772e73c031446a88bdd0715a063121/summary.json`。
Scout1.27.0扫描18个实际镜像，2026-11-07T08:48:29Z完成，扫描错误为零、前后容器清单一致。
当前运行范围和全部盘点范围均为`failed_vulnerability_gate`；退出码2符合有漏洞的完整扫描结果。

| 运行服务 | 旧error/warning | 新error/warning | 剩余主要问题 |
|---|---:|---:|---|
| Web | 8/12 | 1/0 | musl1.2.6-r3，官方当前仓库未提供报告所需r6 |
| API/Worker/Beat（各自） | 2/6 | 2/1 | Debian zlib两项error、tar一项warning，报告无修复版本 |
| MinIO | 95/57 | 95/52 | 固定旧二进制内Go依赖，基础Debian包 |
| PostgreSQL/pgvector | 46/35 | 46/35 | 本轮未升级，gosu内Go依赖及系统包 |
| Nginx | 3/5 | 3/5 | 本轮未升级，musl/zlib/libxml2 |
| Redis | 0/2 | 0/2 | 本轮未升级，busybox/util-linux |

这些数字是扫描器结果级别计数；同一CVE可能影响多个镜像，不能相加当作独立漏洞数。
note/none完整保留在原SARIF中。旧报告用于修复效果比较，新报告才绑定当前实际镜像。

已只读核验官方候选摘要（`.cache/runtime-official-candidates.json`）：pgvector pg16仍等于当前固定manifest；
Nginx与Redis存在新manifest但未拉取或部署，后续须评估兼容性、重启/持久化和最新摘要复扫。
MinIO涉及存储兼容性，不能靠未经核验的新二进制覆盖或自动格式迁移处理告警。

## Nginx 后续修复

官方Nginx新manifest解析后实际Image ID与原运行镜像相同，候选仍3error/5warning；
Redis官方同版7.4.12候选仍2warning。因此没有把这些元数据更新直接作为漏洞修复，Redis原卷与镜像保持原状。
候选SARIF与摘要：`.cache/scout-reports/candidates-20d9dc29b33a4d3db9d5f860415dc531/summary.json`。

`infrastructure/docker/gateway.Dockerfile`固定上述官方Nginx基础摘要，实际卸载网关未加载的动态模块，
使libxml2/libxslt及图像依赖随apk依赖图移除，并精确安装zlib1.4-r0。没有删改包数据库或仅隐藏元数据。
新镜像保留实际动态链接所需库，`nginx -t`、非root只读/资源/端口限制和TLS/HTTP边界实际验收；
TLS1.2、TLS1.3分别强制握手，仍正常核验证书与主机名。候选扫描1error/0warning，剩余musl。
当前实际运行Image ID完整复扫结果以PROGRESS.md最新节为准，不把候选报告替代运行验收。

实际运行镜像完整复扫已完成：
`.cache/scout-reports/20261107T091734Z-74652bf2c8da4312beaa54a0d3b1376e/summary.json`，
2026-11-07T09:19:21Z，18镜像/无扫描错误/清单稳定。网关Image ID
`sha256:a370e021171162c8cb904684ffd16e2b794d916b863d80f151727012c88e6e9c`为1error/0warning，
其余运行服务结果与上一完整报告一致，当前运行及全部镜像门禁仍失败。完整SARIF均按Image ID命名且hash核对通过。

## 0015 授权内容导入后的复扫

`.cache/scout-reports/20261107T095311Z-ae85e79bd7a74f2c8f107a678748a88f/summary.json`
于2026-11-07T09:58:15Z完成，覆盖18个实际镜像（8个运行服务及已退出的辅助/历史测试镜像）。
Scout1.27.0扫描错误为零，容器清单稳定，18份SARIF的SHA-256均匹配报告。
Web为`sha256:e4902ff34060d769b1d1bb1d9d0a9957ddc9160914813779443f782dbb13d633`，
API为`sha256:1305b458152e8648b39f54d61e5dce5a23393dba66ecdeb00a7ecf2d29f07b2b`，
Worker/Beat各自实际Image ID也在报告中绑定，不能以旧镜像报告覆盖本轮重建。
error/warning仍为Web/Nginx各1/0、API/Worker/Beat各2/1、MinIO95/52、pgvector46/35、Redis0/2。
运行范围与全部盘点范围均为`failed_vulnerability_gate`；扫描已完成，剩余漏洞未解决。

## 0016 人工笔记修订后的复扫

最终报告`.cache/scout-reports/20261107T103104Z-e54186ffc0954c75a861b6eeea356824/summary.json`，
2026-11-07T10:35:59Z完成。Scout1.27.0覆盖18个实际镜像，8个运行服务与退出辅助/历史测试范围分别记录；
无扫描错误、前后清单稳定，18份SARIF的SHA-256核对全部匹配。
最终Web Image ID=`sha256:7f7fd39f340b4b129d39b7a1f2f3f795f8481ec0e059b8fb7b7bc6d09e73796f`，
API=`sha256:7d54543ab64d888ca1692ddb7b2f57654655c9282b4d8321d1f790499278eabc`；
Worker/Beat的各自Image ID及容器映射见报告。旧0015报告不用于替代本轮扫描。
error/warning仍为Web/Nginx各1/0、API/Worker/Beat各2/1、MinIO95/52、pgvector46/35、Redis0/2。
运行范围与全部盘点范围均为`failed_vulnerability_gate`，退出码2表示完整扫描含未修复漏洞，不能公开上线。

## 来源与笔记筛选后的复扫

最终报告`.cache/scout-reports/20261107T111524Z-5dae4aab45264c2f98e16bb6b5119710/summary.json`，
2026-11-07T11:16:41Z完成，Scout1.27.0覆盖20个实际镜像；8个运行服务与退出辅助/历史测试范围分别记录。
无扫描错误、清单稳定，20份SARIF的SHA-256核对全部匹配。
Web=`sha256:f87a7e75fd0fe778e0a43885479633b805428930d549aa9b907006792fa826ba`，
API=`sha256:dcb2086b302be3bce3a406384e78796c0e23d02d5c3435c8691f706d72272e39`，
Worker/Beat实际ID和全部容器映射见报告。Web包含全部筛选字段的独立htmlFor/useId修复。
error/warning仍为Web/Nginx各1/0、API/Worker/Beat各2/1、MinIO95/52、pgvector46/35、Redis0/2；
运行与全部范围仍为`failed_vulnerability_gate`，退出码2，不代表公开发布门禁通过。

首次报告`20261107T110448Z-cd0c97e2b67e4c6e81ba27ea8bebe5aa`生成20份有效SARIF，
但浏览器临时Web结束触发CONTAINER_INVENTORY_CHANGED，整体严格failed_scan。
只读比较确认唯一差异为该临时容器移除；全部清理结束后重新执行得到上述最终稳定报告，没有篡改或隐藏失败。

## 0017 人工主题与标签后的复扫

最终报告`.cache/scout-reports/20261107T120621Z-3e32c4c1eb9a4a17b8093ced748bd554/summary.json`，
2026-11-07T12:12:21Z完成。Scout1.27.0扫描20个实际镜像，包括8个运行服务和保留的退出辅助/历史测试范围。
零扫描错误、清单稳定，20份SARIF的SHA-256全部独立核对匹配；临时浏览器容器已在扫描前清理。
API=`sha256:19ab96d35d0a87757fbf49ca90e54e64ff3733d4ceb3751d8ec7d84ae190f508`，
Web=`sha256:a99042a9b3f7be48ddc76ad14d525d8c9e2f97e6e33ee52e957be3e0a056d549`；
Worker/Beat各自实际Image ID及全部容器映射见报告。旧筛选模块报告不能替代当前新镜像证据。
error/warning仍为Web/Nginx各1/0、API/Worker/Beat各2/1、MinIO95/52、pgvector46/35、Redis0/2。
运行和全部范围均为`failed_vulnerability_gate`，退出码2表示扫描完整但漏洞未解决，公开上线门禁继续未通过。
