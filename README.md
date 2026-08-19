# 艾泽拉斯交易所（WoW Auction）

魔兽世界拍卖行快照采集、完整性校验、持久化和在线查询项目。游戏插件负责调用 WoW 拍卖行 API 获取快照；本项目后半段将快照保存到 CloudBase 私有对象存储，校验后写入 MySQL，再由 FastAPI 和静态网站展示真实数据。

## 当前状态

2026-08-20 的第一份生产快照已经完成端到端验证：

- 原始拍卖记录：`380,668`
- 基础物品 ID：`12,759`
- 网站市场条目：`13,303`（战斗宠物按物种拆分）
- 战斗宠物：`545` 个市场分组、`1,210` 条明细
- 总数量：`42,894,416`
- 核心字段缺失、API 错误、单价差异、聚合差异：均为 `0`
- 快照 SHA-256：`8c60305ed1c19b7031b150012b72cab809a474dc45c1d6a14857d596ce67f961`

线上入口：

- 网站：<https://raidbot-5gh3h2nx762bedc5-1251932919.tcloudbaseapp.com/wow-auction/>
- API 健康检查：<https://wow-auction-api-273424-4-1251932919.sh.run.tcloudbase.com/health>
- 市场状态：<https://wow-auction-api-273424-4-1251932919.sh.run.tcloudbase.com/api/market/status>

服务器/连接区元数据尚未写入本次插件快照，因此网站会明确显示“未标注”，不会猜测服务器。

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

## 用户体系

用户注册和登录完全使用 CloudBase 原生身份认证，不在本项目数据库中保存密码：

- 邮箱验证码注册
- 邮箱或用户名 + 密码登录
- 会话恢复与退出
- 邮箱验证码找回密码

市场浏览保持公开只读。浏览器只包含 CloudBase Publishable Key；Server API Key、Client Secret、数据库密码和导入令牌均不进入前端或 Git。

## 公开 API

- `GET /api/market/status`
- `GET /api/market/items?q=&page=&page_size=&sort=`
- `GET /api/market/items/{item_id}/listings?page=&page_size=&battle_pet_creature_id=`

排序支持 `price_asc`、`price_desc`、`quantity_desc`、`listings_desc`、`name_asc`。查询战斗宠物详情时必须带回列表返回的 `battlePetCreatureID`。

管理导入接口使用独立 Bearer 令牌：

- `POST /api/admin/import`：提交私有对象存储签名 URL 和解压后 Lua 的 SHA-256，返回 HTTP 202 + `jobId`
- `GET /api/admin/import/{jobId}`：查询导入进度

管理接口只接受腾讯 COS/CloudBase 的 HTTPS 地址，并限制重定向、端口、下载时间、压缩包和解压大小。

## 本地开发

要求 Python 3.13、[uv](https://docs.astral.sh/uv/) 和 Node.js。

```powershell
uv sync --frozen
$env:PYTHONPATH = "backend"
uv run --frozen wow-auction-import data/auction.lua --json
uv run --frozen uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port 8000
```

认证 SDK 已固定并打包为同源静态文件：

```powershell
npm ci
npm run build:cloudbase
```

运行测试：

```powershell
$env:PYTHONPATH = "backend"
uv run --frozen python -m unittest discover -s backend/tests -v
node --check frontend/js/app.js
node --check frontend/js/auth.js
npm audit
```

## 目录

- `game/addons/AuctionSearchExample/`：游戏插件（当前由独立任务继续加固）
- `backend/`：FastAPI、导入器、SQLAlchemy 模型和测试
- `frontend/`：无框架静态网站和本地打包的 CloudBase Auth SDK
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

## License

MIT
