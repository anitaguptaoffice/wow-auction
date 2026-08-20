# 拍卖数据服务

## 本地导入

```powershell
$env:PYTHONPATH = "backend"
uv run --frozen wow-auction-import data/auction.lua --json
```

默认写入 `data/wow-auction.db`。生产环境通过 `DATABASE_URL` 使用
`mysql+pymysql://user:password@host:3306/database`。导入会先校验 scan 声明条数、
核心字段、`hasAllInfo` 和物品链接，再在单个事务中分批写入。相同文件 SHA-256
不会重复写入；内容指纹用于识别不同 scan。

## HTTP API

- `GET /api/market/status`
- `GET /api/market/catalog`
- `GET /api/market/items?scan_id=&q=&page=&page_size=&sort=`
- `GET /api/market/items/{item_id}/listings?scan_id=&page=&page_size=&battle_pet_creature_id=`
- `GET /api/market/items/{item_id}/history?scan_id=&battle_pet_creature_id=`

普通物品按基础 `itemID` 汇总，`variantCount` 表示其中不同 `itemLink` 数；原始明细
不会丢弃。笼装战斗宠物的共同 `itemID=82800`，因此市场列表额外按
`battlePetCreatureID` 拆分，并返回可供前端使用的 `marketKey`。加载宠物详情时必须
把该 ID 作为 `battle_pet_creature_id` 查询参数传回。
目录接口只列出插件实际写入 `regionID/realmID` 的服务器和时间点。`scan_id` 决定列表、
排行和挂单详情使用哪一份快照；历史接口只比较同服务器且不晚于所选时间点的 scan。
物品的 `marketScope` 只读取插件随该 scan 写入的数据，不使用离线映射兜底。

## 云端导入

配置 `IMPORT_ADMIN_TOKEN` 后：

1. `POST /api/admin/import`，Bearer 鉴权，body 为
   `{"sourceUrl":"https://.../snapshot.tgz","expectedSha256":"..."}`；立即返回
   HTTP 202 和 `jobId`。
2. 轮询 `GET /api/admin/import/{jobId}`，直到 `status` 为 `complete` 或 `error`。

需要全量重载时，`POST /api/admin/reset-market` 还要求 body
`{"confirm":"DELETE_WOW_AUCTION_MARKET_DATA"}`；它只重建本项目 SQLAlchemy 模型声明的
`wow_auction_*` 表，必须在原始归档上传并验证后使用。

下载地址仅允许腾讯 COS/CloudBase HTTPS 域名；服务拒绝重定向，并限制下载时长、
压缩包和解压大小。tgz 必须只含根目录下一个 `auction.lua`，解压后 SHA 匹配才会
入库。同一进程只允许一个任务，MySQL 另用 `GET_LOCK` 做数据库级互斥。

后台任务状态保存在实例内存中，因此 CloudBase Run 必须固定 `MaxNum=1`；导入期间
保持 `MinNum=1`，任务完成后可恢复 `MinNum=0`。
