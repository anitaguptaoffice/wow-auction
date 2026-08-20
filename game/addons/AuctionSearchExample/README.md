# AuctionSearchExample

打开拍卖行后，插件通过 `C_AuctionHouse.ReplicateItems()` 请求当前服务器的完整拍卖快照，并在屏幕顶部显示状态面板：

- 请求阶段：等待服务器返回快照（该接口有约 15 分钟账号级限流）
- 扫描阶段：显示已处理数量、总数、百分比和进度条
- 完成阶段：显示实际保存条数、耗时及包含 `itemLink` 的条数；核心字段有缺失时会显示橙色警告

进入角色后先显示 `AS_READY`；打开拍卖行后，状态面板会在固定位置显示供自动化 OCR 识别的稳定标签：`AS_WAITING`、`AS_SCANNING`、`AS_COMPLETE`、`AS_WARNING` 或 `AS_ERROR`。这些标签只显示在面板中，不会写入聊天框。

正常扫描不会向聊天框输出批次日志。调试命令：

- `/as stats`：查看已保存扫描统计
- `/as history <物品ID>`：查看指定物品的最近记录
- `/as test [物品ID]`：检查客户端物品缓存
- `/as uitest scanning`：预览进度面板（也可传入 `started`、`complete`、`warning` 或 `error`）
- `/as clear`：清空插件保存的数据

完成后退出游戏或执行一次 `/reload`，WoW 才会把本次快照写入磁盘上的 `AuctionSearchExample.lua`。
全量快照体积较大，插件默认保留最近 7 天，并且同一天只保留最新一次扫描。

每次扫描还会记录当前 `region`、`realm`，并按唯一 `ItemID` 保存一次市场范围：

- `region`：区域共享商品（Commodity）；
- `realm`：当前服务器/连接区独有物品；
- `unknown`：客户端暂未返回商品状态，网站会显示“范围待确认”。

插件面向 WoW 12.1，TOC 接口版本为 `120100`。
