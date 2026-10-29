# 自动更新、增量导入和市场浏览

外部程序新增快照优先使用 [HTTP 增量同步接口](SNAPSHOT_SYNC_API.md)：只上传一份 `snapshot.jsonl.gz`，Bearer 鉴权，服务器负责导入与发布。下文的 SSH 整库发布继续作为兼容管理路径；不要让旧版本发布器与新同步工作进程同时运行。

## Windows 一键同步

本机设置好 `deploy/update.local.json`、`deploy/publish.local.json` 和 SSH 密钥后，双击仓库根目录的 **同步拍卖数据.cmd** 即可。它会自动查找客户端 SavedVariables 或采集器归档，按采集时间选择新快照，完成归档、校验、增量导入、图标补全、验收和线上切换。示例及本机使用 `realm_policy: discover`：新服务器自动接入，不需要逐服改名单、写导入或验收脚本。没有新数据时不会发布。游戏需要先正常退出或重载界面，将数据写入磁盘；正在写入的文件会被稳定性检查拒绝。

```powershell
# 只预检新数据，不导入、不上传
./scripts/sync-market.ps1 -Preview
# 自动发现并同步
./scripts/sync-market.ps1
# 明确指定某份 Lua 或带清单的压缩归档
./scripts/sync-market.ps1 -Source 'F:/path/AuctionSearchExample.lua'
# 仅准备候选，不上线
./scripts/sync-market.ps1 -PrepareOnly
# 通用线上核验：读取当前发布报告检查所有市场，不导入或上传
./scripts/sync-market.ps1 -Verify
```

发现阶段只读文件头作候选筛选，正式导入仍校验完整快照。`archive_roots` 可填写现有采集器的归档目录，会检查其中 `auction-*.lua.tgz`，适用于采集器已清空客户端原文件的情况。不同客户端序列化方式导致文件头无法识别时，可用同一命令的 `-Source` 指定，无需另外写脚本。路径、服务器策略和密钥别名均在本地配置中，不提交到 Git。对外不新增上传或重置接口，数据通过 SSH 发布。

本地开发完成后仍需单独发布代码。以下配置默认只准备候选版本；不会因为运行采集器而自动上线。示例不含密码，SSH 发布使用已配置的密钥或别名。

## 配置与运行

复制 `deploy/update.example.json` 为 `deploy/update.local.json`，修改种子数据库和归档路径。配置中的数据路径相对配置文件；命令参数用 `{python}`、`{repo}`、`{config_dir}`、`{generation}`、`{database}`、`{report}`、`{base_sha256}` 占位符。命令必须为 argv 数组，不经 shell 拼接。

已有服务器的新快照必须晚于该服最新数据。自动发现模式下，新服务器还必须晚于 `new_realm_after` 截止线，以免把过去明确移除的历史误导回去；未指定时以第一次更新前的最新采集时间建立截止线，并随版本保存，后续不会移动。可用 `excluded_realms` 显式排除服务器。正式导入会再次用完整解析结果验证策略，不信任文件头提示。

若希望只维护固定服务器，可显式使用 `realm_policy: allowlist` 和 `allowed_realms`。旧配置未写策略时保留此行为；预检会提示被白名单挡住的数据。目录扫描 `sources` 应指向准备新增的收件目录；客户端自动发现则结合上述时间策略过滤已有游戏文件和归档。需要补导历史时使用显式 `manage stage --add`。

```powershell
$env:PYTHONPATH = 'backend'
./.venv/Scripts/python.exe -X utf8 -m compact_market.update --config deploy/update.local.json
# 可选：持续扫描，至少每 5 秒检查一次；不要与采集器导入方式重复运行
./.venv/Scripts/python.exe -X utf8 -m compact_market.update --config deploy/update.local.json --watch 30
```

流程：稳定复制客户端 Lua（或读取带清单的归档）→ SHA/完整性校验 → 去重追加候选库 → 图标映射及补全 → 只读 API 数据契约验收 → 保存本地可用版本。未变化的输入不再构建。失败保留旧 `current.json`，记录 `status.json` 和 `jobs/任务ID.json`，可以修复输入后重试；进程中断留下的独占锁需确认 PID 已退出后移除。`current.json` 代表本地已验收版本，`publication` 非空才表示收到线上发布成功回执。

服务器在切换前和切换后复用同一个通用核验器：从报告生成预期目录，逐份核对时间和原始记录总数，再检查各市场的状态、物品、挂单分页和历史归属。核验失败恢复旧路由。回执包含核验结果；终端直接显示新增服务器、快照数和记录数。`-Verify` 使用同一套规则只读复核当前线上，结果写入 `verification.json`，正常同步无需再编写按服核验脚本。

`icon_map` 可使用现有映射，`listfile` 可补充新的 FileDataID 名称。未知名称保存为 `filedata-ID`，图标同步器按 ID 补全，已缓存图标和 CDN 状态会沿用；缺失图片保留占位，不阻止价格更新。希望图片缺失也阻止发布时，在 `icon_sync_command` 加 `--strict`。图标状态保存在每个候选目录。映射通过 API 随物品返回，新物品无需重新构建前端静态映射。

### 连接现有客户端采集器

保留现有采集、退出游戏落盘和归档流程，只将本地 `wow-runner` 配置改为：

```yaml
snapshot:
  import_enabled: true
  python_exe: '../../.venv/Scripts/python.exe'
  importer_script: '../../backend/import_compact.py'
  compact_update_config: '../../deploy/update.local.json'
  clear_source_after_import: false
```

采集器将配置路径传给导入器；也可通过进程环境变量 `COMPACT_UPDATE_CONFIG` 指向配置绝对路径。新入口兼容原导入器的 JSON 回执（源 SHA、导入/跳过数量、扫描 ID），不读 `database_url`，不修改游戏文件。默认 `publish: false`；明确启用发布后，只有发布回执确认成功才向采集器报告成功。

## 显式启用发布

复制 `publish.example.json` 为 `publish.local.json`，确认 SSH 密钥登录及主机指纹已配置。首次启用前，种子库 SHA 必须对应线上当前数据；若多人另行换库，预期 SHA 检查会阻止覆盖。

```powershell
./.venv/Scripts/python.exe -X utf8 -m compact_market.update --config deploy/update.local.json --publish
```

采集器模式通过本地配置 `publish: true` 开启；目录扫描模式必须显式传 `--publish`。先前已准备但未发布的候选也可用同一命令发布。

发布器通过 OpenSSH 上传到独立临时目录，再原子改名为正式候选目录，绝不重新覆盖已存在的数据库。服务器从 Nginx upstream 和 Docker 端口查找当前 API，沿用不可变镜像 ID，在 8180/8181 交替启动候选容器。验证数据库及图标映射 SHA、完整市场清单、分页挂单和公网接口，之后切换路由。失败恢复旧路由；成功保留旧库、旧容器及回滚清单。此入口仅切换数据，不发布前端或 API 镜像。

回滚仍使用 `deploy/dataset.py rollback 回滚清单.json`。执行回滚或人工部署后，须重新对齐更新器的种子/已发布 SHA 再继续，预期 SHA 检查不会静默覆盖人工操作。

若发布进程中断或 SSH 回包丢失，任务记录 `publication_unknown`，停止后续自动发布。先读取服务器候选目录的 `publication.json`；若不存在，用同一 `publish_update.py` 命令重试确认，服务端会根据活动数据库和回滚记录恢复回执。拿到正确回执后：

```powershell
./.venv/Scripts/python.exe -X utf8 -m compact_market.update --config deploy/update.local.json --reconcile-receipt data/compact/publication.json
```

如果核实远端未切换、旧库仍正常，可将 `status.json` 的状态由 `publication_unknown` 改为 `failed` 后重试；不要在没有核实远端结果时清除状态。SSH 发布器会保存服务器发布状态到共享 `runtime/status.json`，新候选容器挂载该目录并设置 `COMPACT_UPDATE_STATUS_FILE`，后续发布失败时页面提示仍使用旧快照。此提示需使用本次代码版本的 API；首次接入之前的旧容器不会识别它。客户端采集/导入失败会留在本地任务记录中，若也希望在网站展示，需要另行同步本地状态；网络中断时不会假设服务器已收到失败通知。

## 增量和校验边界

候选库从旧库文件克隆，旧库保持只读。新快照合并到共享字典，并直接追加已验证的新摘要；旧快照不重新解析、不重算历史摘要。删除只处理指定快照，再清理孤立字典。扫描 ID 永不复用。

只有基库 SHA 与其报告完全一致、逐快照行数/数量/映射摘要也吻合时，才继承旧快照的完整校验证据；无可信报告时仍逐条校验旧数据。新数据始终进行完整往返校验，可用 `manage stage --full-verify` 强制审计所有历史。

这仍是单一 DuckDB 文件：候选发布需要一次文件复制和 SHA 顺序读取，峰值磁盘需要容纳旧库和新库，并非常数时间导入。暂不引入自定义差值/RLE 格式；DuckDB 负责列式压缩，Arrow 对历史摘要字符串做字典编码。当前改动减少重复聚合和 Python 解码，不声称已验证半年高频历史容量。最新市场保留常用搜索/排序索引，历史排序按需建立、最多缓存 16 组，避免为每份历史快照常驻五份排序索引。

## 市场和历史的语义

- `全部市场`：各市场自己的最新快照混合浏览，不以一个全局时间代表所有服务器；每条报价标注真实采集时间。
- `全服商品`：区域共享商品，按其独立的快照时间查看；具体服务器选项仅显示该服市场。
- 选择市场后可选择历史快照，URL 保存 `market` 和 `scan_id`；服务器、快照、搜索及装备条件改变后回到第一页。已删除的链接显示错误与重置入口，不静默展示另一份数据。
- 各服比价显示同一市场键（包含物品、档位、宠物变体），沿用当前装备筛选；未筛选的 bonus/属性仍会合并，不能据此推断为完全相同的具体装备。每服标注采集时间，支持打开该服挂单。
- 历史沿用装备条件，支持截至当前快照的全部/7/30/90天。完整快照中物品或符合条件的挂单不存在时，返回 `absent` 和空价格；仅竞价时返回 `no_price`；均断开曲线，不填零价格。首次观察之前不生成虚构的物品历史。
- API 支持 `start`、`end` 和 `max_points`（8–2000，默认400）。分桶保留首末和价格极值，被压缩掉的缺口也通过 `breakBefore` 保留；涨跌使用未抽样的最后两次观察。
- 采集失败不会生成“物品下架”快照；实际采集之间的市场状态未知。若已明确固定采集周期，设置 API 的 `COMPACT_EXPECTED_INTERVAL_SECONDS`，超过周期1.5倍的区间断线；未设置时不猜测缺失的计划采集。
