# 艾泽拉斯交易所（WoW Auction）

## Single-server deployment (2026-10-30)

The active migration branch uses the existing React frontend with the compact
DuckDB/Arrow API. The CloudBase architecture documented below is legacy; the new
runtime does not require CloudBase, MySQL, or Redis.

- Frontend: `https://wowplayer.fun/`, served by the existing host Nginx.
- API: one Docker process, loopback port `8181`, 2 GiB container limit; do not scale
  workers without budgeting another complete in-memory history index per worker.
- Data: immutable DuckDB generations and original compressed snapshots outside Git.
- Validation: 69 backend tests; public desktop/mobile search, detail pagination and
  three-point history verified. Original migration loopback test: 800 requests, 8 concurrent,
  zero errors; P95 list/search/history about 116 ms, detail 189 ms with a 2-CPU limit.
  These are server measurements, not Internet round-trip timings.
- Current dataset: four verified source snapshots, 1,756,007 original listing rows.
  Silver Hand retains its October 29 02:58:50 snapshot and the new October 30
  00:37:52 snapshot (UTC+8); Panda Brewmaster has October 30 00:13:58 and
  Deathwing October 30 00:42:41. Earlier August/October snapshots are no longer
  in the active dataset. Shared commodity history is derived from these four sources.
- Active database: `/opt/wow-auction/data/client-20261030-four/market.duckdb`;
  active API container: `wow-auction-data-20261030-four`, loopback port `8181`.
  The data-only switch uses the previous API image and leaves the frontend unchanged.
  The previous API container and database are retained for rollback.
  Dataset verification is recorded in `experiments/compact-market/results/client-four-generation-20261030.json`.
- Region commodities use market server ID `0`; each actual realm has its own history.
  Default browsing combines only the latest observation from each market.
- Accounts and automatic snapshot collection are not enabled on this deployment.

### Build and release

```sh
uv sync --frozen --group compact
uv run --group compact python -m unittest discover -s backend/tests -v
npm ci
npm run typecheck
npm run build
docker build -f deploy/Dockerfile -t wow-auction:RELEASE .
docker save wow-auction:RELEASE | gzip > image.tar.gz
tar -czf web.tar.gz -C frontend/dist .
```

Transfer artifacts to `/opt/wow-auction/releases/RELEASE/` using SSH. Never transfer
credentials into the image. On the host, load the image and start a candidate on
an unused loopback port (alternate `8180` and `8181` for subsequent releases):

```sh
docker load -i image.tar.gz
bash run-api.sh wow-auction:RELEASE /absolute/path/market.duckdb wow-auction-RELEASE 8181
```

`deploy/run-api.sh` waits for health and fails if startup fails. Keep the current
container running during candidate validation. Extract `web.tar.gz` into the release's
`web/` directory. Back up the existing site config and public directory; point
`/var/www/wowplayer.fun/public` at that release's `web/`. Include
`deploy/nginx-api.conf` in the existing HTTPS server block, adjusting the candidate
port. Validate with `nginx -t`, then reload. Check the public homepage, catalog,
search, listings, and history before stopping the previous API container.

Rollback restores the previous public-directory symlink and Nginx upstream port,
starts the previous container if stopped, then validates and reloads Nginx.
Retain previous image, frontend, and database together; API and frontend contracts
must be rolled back as a pair. Cloudreve is a separate application and is untouched.

### Import another snapshot

Use `compact_market.manage` to list, archive, add, remove or retain exact source
snapshots. It writes a new immutable generation, preserves existing IDs, skips
reimports and fully verifies new rows. Retained rows inherit verification only
when the base file SHA and per-snapshot evidence match; `--full-verify` audits all
rows. Old summaries are reused instead of being recomputed. Data-only blue/green switching
and rollback use the existing API image and leave the frontend untouched.
See [snapshot management](deploy/SNAPSHOT_MANAGEMENT.md) for complete commands.
See [automatic updates and market history](deploy/AUTOMATIC_UPDATES.md) for the
opt-in runner/watch pipeline, candidate acceptance and SSH publication. This is
  functionality; automatic publication remains disabled by default. On Windows,
  use the root `同步拍卖数据.cmd` entry after configuring the local settings, or
  `./scripts/sync-market.ps1 -Preview` to discover new client data without uploading.

The lower-level `compact_market.build` command still rebuilds a generation from
all supplied originals. Keep each archive beside its manifest. Both the runner's
`*.lua.manifest.json` and content-addressed `manifest.json` are supported.

```sh
uv run --group compact python -m compact_market.build \
  data/archive/compact-source/*/auction.lua.tgz \
  --output data/compact/market-NEXT.duckdb
```

Run this on a build machine initially (measured peak memory 2.52 GiB for the initial
three archives). Refresh icon mappings and fallback assets before building the frontend/API release:

```sh
curl -fL https://github.com/wowdev/wow-listfile/releases/latest/download/community-listfile.csv -o /tmp/community-listfile.csv
uv run --group compact python scripts/generate_icon_manifest.py data/compact/market-NEXT.duckdb /tmp/community-listfile.csv
python3 scripts/sync-icon-cache.py --strict
```

The default pipeline tries named CDN icons, Wowhead numeric FileDataID icons, then
remote CASC extraction. Use `--skip-casc` only for a deliberately limited CDN check.
`--strict` prevents silently publishing unresolved icons.
New item data is discovered during import, but new texture IDs also require this
asset refresh. Deploy frontend and API allowlists together.

Transfer the result, verify SHA-256, and start a candidate container
with that generation using the same release procedure. Never replace a database
file underneath a running reader. More frequent ingestion, incremental history
compaction, off-host backups, and scheduled collection remain follow-up work.
Current memory and latency measurements cover three snapshots, not months of data.
See `experiments/compact-market/README.md` for the original benchmark evidence.

---

## Legacy CloudBase deployment

魔兽世界拍卖行快照采集、完整性校验、持久化和在线查询项目。游戏插件负责调用 WoW 拍卖行 API 获取快照；本项目后半段将快照保存到 CloudBase 私有对象存储，校验后写入 MySQL，再由 FastAPI 和静态网站展示真实数据。

## 当前状态

2026-08-21 已完成三组服务器、四个时间点的端到端验证：

- 白银之手：`509,620`、`503,601` 条（两个时间点）
- 熊猫酒仙：`457,472` 条
- 凤凰之神：`491,099` 条
- 云端总记录：`1,961,792`
- 核心字段缺失、API 错误和插件范围未知项：均为 `0`

线上入口：

- 网站：<https://raidbot-5gh3h2nx762bedc5-1251932919.tcloudbaseapp.com/wow-auction/>
- API 健康检查：<https://wow-auction-api-273424-4-1251932919.sh.run.tcloudbase.com/health>
- 市场状态：<https://wow-auction-api-273424-4-1251932919.sh.run.tcloudbase.com/api/market/status>
- 服务器与时间点：<https://wow-auction-api-273424-4-1251932919.sh.run.tcloudbase.com/api/market/catalog>

新版插件会把区域、服务器以及每个物品的市场范围写入快照。范围直接采用游戏客户端的
`C_AuctionHouse.GetItemKeyInfo(...).isCommodity`：商品标记为“区域共享”，非商品标记为
“服务器独有”；旧快照没有该信息时显示“范围待确认”，不会猜测。

## 数据流

```text
WoW 客户端
  -> AuctionSearchExample 插件生成 SavedVariables 快照
  -> auction.lua + SHA/manifest
  -> CloudBase 私有对象存储 wow-auction/snapshots/
  -> FastAPI 管理导入（校验、去重、事务、批量写入）
  -> CloudBase MySQL 的 wow_auction_* 表
  -> 公开只读市场 API
  -> CloudBase 静态托管 /wow-auction/
```

原始数据每一行都会保存到 `wow_auction_listings`，网站摘要是额外的物化视图，不会替代或丢弃原始行。普通装备按基础 `itemID` 汇总，并返回 `variantCount`；完整 `itemLink` 保留在明细中。笼装战斗宠物共享 `itemID=82800`，因此额外按 `battlePetCreatureID` 分组。

本地采集时插件使用角色级 SavedVariables，并且每个角色只保留最新一份快照。Windows runner 在每个角色正常刷盘后立即执行完整性校验、gzip 归档、数据库幂等导入和条数复核；仅最后一个角色令 `Wow.exe` 完全退出且上述步骤全部成功后，才按各文件 SHA-256 原子清空本轮已处理的角色文件。任何失败都会保留源数据供重试。

## 用户体系

用户注册和登录完全使用 CloudBase 原生身份认证，不在本项目数据库中保存密码：

- 邮箱验证码注册
- 邮箱或用户名 + 密码登录
- 会话恢复与退出
- 邮箱验证码找回密码

市场浏览保持公开只读。浏览器只包含 CloudBase Publishable Key；Server API Key、Client Secret、数据库密码和导入令牌均不进入前端或 Git。

## 公开 API

- `GET /api/market/status`
- `GET /api/market/catalog`
- `GET /api/market/items?scan_id=&q=&page=&page_size=&sort=`
- `GET /api/market/items/{item_id}/listings?scan_id=&page=&page_size=&battle_pet_creature_id=`
- `GET /api/market/items/{item_id}/history?scan_id=&battle_pet_creature_id=`

排序支持 `price_asc`、`price_desc`、`quantity_desc`、`listings_desc`、`name_asc`。查询战斗宠物详情时必须带回列表返回的 `battlePetCreatureID`。
`scan_id` 选择插件标注服务器下的具体时间点。历史接口只比较同一 `regionID + realmID`
且不晚于所选时间点的快照，避免跨服务器串价。

管理导入接口使用独立 Bearer 令牌：

- `POST /api/admin/import`：提交私有对象存储签名 URL 和解压后 Lua 的 SHA-256，返回 HTTP 202 + `jobId`
- `GET /api/admin/import/{jobId}`：查询导入进度

管理接口只接受腾讯 COS/CloudBase 的 HTTPS 地址，并限制重定向、端口、下载时间、压缩包和解压大小。

## 本地开发

要求 Python 3.13、[uv](https://docs.astral.sh/uv/) 和 Node.js。先安装依赖：

```powershell
uv sync --frozen
npm ci
```

导入离线快照并启动 API：

```powershell
$env:PYTHONPATH = "backend"
uv run --frozen wow-auction-import data/auction.lua --json
uv run --frozen uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port 8000
```

另开一个终端启动 React 前端；Vite 会把 `/api` 代理到本机 `8000` 端口：

```powershell
npm run dev
```

访问 <http://127.0.0.1:5173>。生产构建输出到 `frontend/dist/`：

若只需要调试前端而不启动本地 API，可临时设置 `VITE_API_BASE` 指向只读 API。
若要在没有任何 API/快照时检查交互，可用 `VITE_USE_MOCK_DATA=true npm run dev`；mock 只在 Vite 开发环境启用，不会进入生产数据路径。

物品图标根据快照中的 WoW `texture FileDataID` 查询生成清单，运行时默认从 Blizzard 官方图标 CDN 加载。导入包含新图标的数据后，可下载最新 `community-listfile.csv` 并重新生成清单：

```bash
npm run icons:generate -- data/wow-auction.db /path/to/community-listfile.csv frontend/src/generated/icon-map.json
```

发布新快照时直接运行 CloudBase 构建。它会下载最新 community listfile、更新前后端图标清单，检测 Blizzard CDN，并只把官方缺失的图标缓存进静态资源：

```bash
npm run build:cloudbase
```

2026-10-29 补图记录：21 个家宅装饰图标用 community listfile 长文件名访问两个 JPEG 源均失败，但 Wowhead 数字 FileDataID 地址全部可用。例如物品 `284240` 的 tooltip 返回 `icon: "8106986"`，对应 `https://wow.zamimg.com/images/wow/icons/large/8106986.jpg`。同时已按原始 FileDataID 从 CASC 导出全部 21 个 WebP 并部署；当前图标清单没有 `missing` 项。脚本现依次尝试官方文件名、备用文件名、备用数字 ID、CASC。验收记录见 `experiments/compact-market/results/icon-recovery.json`。

同步结果持久化在 `frontend/src/generated/icon-source-status.json`；该完整状态仅供发布工具使用，不会打进前端包。前端只读取自动生成的 `local-icon-map.json`，其中仅包含已固化静态图标的文件格式。后续发布会跳过已经确认可走 Blizzard CDN 的图标，并验证已经固化的静态文件，只处理新出现的图标和之前未补成功的 `missing`。新纹理即使没有 community listfile 文件名，也会以 `filedata-<FileDataID>` 合成名称进入清单并直接从 CASC 导出，不会静默漏掉。新图标先检查默认 CDN；默认 CDN 不可用或状态为 `missing` 时，自动依次尝试备用 JPEG 和 CASC，并把成功结果统一写入静态资源目录。需要主动重新核验全部官方 CDN 状态时运行 `npm run icons:sync -- --refresh`。

Blizzard CDN 缺失的图标会统一补入 `frontend/public/wow-icons/`：优先下载备用 JPEG（先用文件名，再用数字 FileDataID）；JPEG 镜像尚未收录的新图标，则由完整的 `icons:prepare` 使用固定版本的 `wowdata` 按 `FileDataID` 从国服 Retail CASC 导出 WebP。两种格式都属于同一个 TCB 本地静态来源，状态清单会告诉前端直接请求正确格式，不会逐个试探。最终仍无法导出的资源只会告警，前端使用内置占位，不会阻断整站发布；需要在 CI 中把这类告警视为失败时，可运行 `npm run icons:sync -- --strict`。

清单生成器同时接受 `interface/icons/` 和 `housing/icons/`，并把路径中的字面空格转换为 CDN 使用的 `-`，避免把可用图标误判为缺失资源。

运行时仍有受清单限制的 `/api/icons/{icon_name}.jpg` 兜底代理；首次成功后使用磁盘缓存，并向浏览器返回一年不可变缓存。中国大陆客户端不会直接请求备用图标源。

`VITE_WOW_ICON_BASE_URL` 可在构建时改为 TCB 静态资源目录；该目录应保存为 `<icon-name>.jpg`。无法解析或加载失败的图标会自动回退到字形占位，不显示破图。

```powershell
npm run typecheck
npm run build
```

运行测试：

```powershell
$env:PYTHONPATH = "backend"
uv run --frozen python -m unittest discover -s backend/tests -v
npm run typecheck
npm run build
npm audit
```

## 目录

- `game/addons/AuctionSearchExample/`：游戏插件（当前由独立任务继续加固）
- `backend/`：FastAPI、导入器、SQLAlchemy 模型和测试
- `frontend/`：React + TypeScript + Vite 前端；生产构建位于 `frontend/dist/`
- `data/`：本地快照、SQLite 和归档；大文件均被 `.gitignore` 排除
- `automation/`：Windows 外部自动化；不属于本次网站部署改动
- `CLOUDBASE.md`：CloudBase 资源、部署和更新运行手册

## 部署约束

- CloudRun 服务固定 `MaxNum=1`；导入任务状态当前保存在进程内存，不能多实例轮询。
- 导入期间临时设 `MinNum=1`，完成后恢复 `MinNum=0`。
- CloudRun 规格为 1 CPU / 2 GiB；真实快照解析峰值约 1.37 GiB。
- MySQL 只开放私网地址，应用账号只拥有目标数据库权限。
- 开启定时更新前需要确定历史快照保留数量；当前不会自动删除历史数据。

详细运行手册见 [CLOUDBASE.md](CLOUDBASE.md)，后端契约见 [backend/README.md](backend/README.md)。
产品定位、阶段范围和非目标见 [PRODUCT.md](PRODUCT.md)。

## License

MIT
