# WoW 拍卖数据自动采集开发计划

## 目标与边界

本任务只负责把一份新的、经过独立完整性校验的拍卖快照写入仓库 `data/auction.lua`。网站部署由另一条任务处理。

自动化不实现账号登录，不输入账号、密码或验证码。可以复用已登录的 Battle.net 会话，并控制 WoW 的选角、进入世界、拍卖师交互、扫描监控和正常退出。

## 已定技术方案

- 主控：Windows Go 程序 `wow-runner`。
- 识别：系统 `Windows.Media.Ocr`，默认语言 `zh-Hans-CN`。
- 插件：WoW 12.1 `C_AuctionHouse.ReplicateItems()` 全量快照接口。
- 输入：Win32 `SendInput`，支持 Home、Down、Enter、组合键和 Unicode 斜杠命令。
- 退出：中间角色 `/logout`，最后一角和失败重试 `/quit`。
- 落盘：WoW 进程自然退出后解析 SavedVariables；通过门禁后原子替换目标文件。
- 安全约束：自动化流程不调用 `TerminateProcess`；正常退出超时就失败并保留客户端。

## 状态机

1. `INIT`：加载并校验配置。
2. `BNET_START`：启动或复用 Battle.net，选择可见主窗口。
3. `WOW_FOREGROUND`：OCR 找到“魔兽世界”和“进入游戏”并点击，等待可见 WoW 窗口。
4. `CHAR_SELECT`：OCR 确认选角页；`current` 保持当前角色，`single/all` 用 Home + Down 定位。
5. `ENTER_WORLD`：按 Enter，等待插件面板出现 `AS_READY`。
6. `AH_PREP`：等待角色可交互。
7. `AH_OPEN`：发送 `/targetexact <拍卖师>` 或宏键，再发送交互键；记录 `scan_trigger_ts`。
8. `WAIT_PLUGIN_SCAN`：OCR 必须先见 `AS_SCANNING`，再稳定读到 `AS_COMPLETE` 或 `AS_WARNING`。`AS_ERROR` 立即失败。
9. `GRACEFUL_EXIT`：多角色中间轮使用 `/logout`；最后一轮使用 `/quit` 并等待进程自然结束。
10. `SNAPSHOT_VALIDATE`：定位、解析、验证 SavedVariables。
11. `DONE`：原子同步完成。

`AS_WAITING` 到 `AS_SCANNING` 之间没有服务端百分比。总等待时间必须覆盖 ReplicateItems 可能出现的约 15 分钟限流。

## 插件状态契约

插件面板提供高对比 ASCII 标记，供 OCR 稳定识别：

- `AS_READY`
- `AS_WAITING`
- `AS_SCANNING`
- `AS_COMPLETE`
- `AS_WARNING`
- `AS_ERROR`

扫描结果的每条 Replicate 行都先保存核心拍卖字段。物品链接和详情是可空的异步信息，不作为“核心行缺失”；插件会分别记录 `linkedItemCount` 与 `incompleteInfoCount`。

## 最终完整性门禁

OCR 完成不能单独证明成功。正常退出后，对即将发布的精确字节执行流式解析并要求：

- 最新扫描时间不早于 `scan_trigger_ts`；
- `itemCount > 0`；
- `itemCount == recordCount == items` 实际记录数；
- `missingCoreCount == 0`；
- `apiErrorCount == 0`；
- `lastScanTime`、最新扫描时间和两份元数据一致。

`linkedItemCount < itemCount` 或 `incompleteInfoCount > 0` 只产生警告。任何结构、时效或核心数据门禁失败时，旧目标文件保持不变。

## 配置要点

- 多账号机器必须设置 `snapshot.account` 或明确的 `snapshot.source`。
- `characters.mode: current` 适合固定一个站在拍卖师旁的角色。
- `characters.mode: all` 按 `indices` 运行多个角色。
- `keys.interact_target` 支持 `ALT-CTRL-H` 等 WoW 风格组合键。
- OCR 主路径下，模板字段可以留空；`ocr.enabled: false` 才使用 NCC 模板回退。
- `timeouts_seconds.max_since_scan_trigger` 建议不少于 1200 秒。

## 验收要求

静态与离线验收：

- `go test ./...`
- `go vet ./...`
- Lua 语法解析
- 使用真实大型 SavedVariables 运行流式解析与记录计数
- 不完整快照不得覆盖旧目标文件

最终实机验收仍需在服务器可用且用户明确允许后执行一轮：

Battle.net → 点击进入游戏 → 选角 → `AS_READY` → 开拍卖 → `AS_SCANNING` → 完成 → `/quit` → 进程自然结束 → `snapshot_validated` → `DONE`。
