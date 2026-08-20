# wow-runner

Windows 自动采集链路：启动或复用 Battle.net → OCR 选择《魔兽世界》并点击「进入游戏」→ 选角 → 等待插件进入世界 → 定位拍卖师并交互 → 显式发送 `/as scan` → OCR 观察扫描状态 → 正常退出 → 校验快照 → 压缩归档 → 追加导入网站数据库 → 成功复核后清空游戏 SavedVariables。

## 安全与成功条件

- 不自动登录账号，不输入账号、密码或验证码。
- 不调用 `TerminateProcess`。中间角色使用 `/logout`，最后一角及超时重试使用 `/quit`；自然退出超时会报错并保留客户端。
- OCR 看到完成只代表插件已在内存中生成快照，不代表文件已落盘。
- 每个角色成功必须在 `/logout` 或 `/quit` 触发 SavedVariables 刷盘后同时满足：
  - 最新扫描时间不早于本轮打开拍卖行的时间；
  - `itemCount == recordCount == items` 实际记录数，且大于 0；
  - `missingCoreCount == 0`、`apiErrorCount == 0`；
  - 校验通过后才原子替换目标 `data/auction.lua`；
  - 创建带快照 SHA-256 的 `.lua.tgz` 和 JSON manifest；
  - 后端导入器返回相同 SHA，且 `imported + skipped == source scans`；
  - 只有最后一个角色令 `Wow.exe` 完全退出后，才按 SHA 再次核对并逐一原子清空本轮已处理的角色源文件。
- `linkedItemCount` 不足或 `incompleteInfoCount > 0` 只记警告；价格、数量等核心拍卖行记录仍须完整。
- 任一归档、导入或复核步骤失败都会保留 WoW 源文件和 `data/auction.lua`，绝不执行清空。

## 使用

```powershell
Copy-Item config.example.yaml config.yaml
# 编辑 config.yaml 后先做只读预检
go run ./cmd/wow-runner -config config.yaml -check
# 执行完整采集流程
go run ./cmd/wow-runner -config config.yaml -run
# 手动操作游戏时：每次 /logout 到选角页后只处理最新角色文件
go run ./cmd/wow-runner -config config.yaml -ingest
```

`config.yaml` 包含本机路径和账号目录，已被 `.gitignore` 忽略。

## OCR 状态

主路径使用系统自带 `Windows.Media.Ocr`（默认 `zh-Hans-CN`），无需 OpenCV。插件面板提供稳定的 ASCII 令牌：

- `AS_READY`：角色已进入世界，插件可工作；
- `AS_WAITING`：已请求拍卖快照，等待服务器；
- `AS_SCANNING`：正在写入扫描记录；
- `AS_COMPLETE`：核心数据完整；
- `AS_WARNING`：核心记录已保存，但部分物品详情尚未就绪；
- `AS_ERROR`：扫描失败。

打开拍卖行本身不会触发全量接口；runner 会明确发送一次 `/as scan`。runner 只接受先观察到 `AS_SCANNING`、再连续稳定读到 `AS_COMPLETE` 或 `AS_WARNING` 的单调序列，避免把上一次残留的完成面板误判为本轮成功。`AS_WARNING` 仍会正常退出，最终由磁盘快照门禁决定成功或失败。

Battle.net 同样由 OCR 定位“魔兽世界”和“进入游戏”；若当前时间落在可识别的维护公告窗口内，runner 会安全停止，不点击进入游戏。`cmd/wow-ocr` 可只读截取指定窗口并输出 OCR 文本，便于校准：

```powershell
go run ./cmd/wow-ocr -exe Battle.net.exe -language zh-Hans-CN
```

## 角色与拍卖行

- `characters.mode: current`：保持选角页当前角色；适合单角色稳定采集。
- `single`：按 Home 后根据 `indices[0]` 向下定位。
- `all`：按 `indices` 顺序逐个角色运行；中间角色正常 `/logout` 回选角页。
- `keys.auctioneer_target` 非空时发送 `/targetexact <名称>`；否则可使用 `auction_tar_macro`。若游戏已将“与附近目标互动”绑定到快捷键，两项都可留空。
- `interact_target` 支持单键、组合键以及 `MOUSEWHEELDOWN` / `MOUSEWHEELUP`。例如把“与附近目标互动”绑定到滚轮向下，可直接打开角色身旁的拍卖师。
- `logout_macro` 可填写动作条上的 `/logout` 宏键（例如 `1`）；留空时回退为聊天命令 `/logout`。

## 快照归档与导入

`snapshot` 配置控制游戏外持久化：

- `destination`：最新验证副本，默认 `data/auction.lua`；
- `archive_dir`：每轮压缩归档与 manifest 目录；
- `import_enabled`：调用 `backend/import_auction.py` 追加数据库；
- `python_exe`、`importer_script`：Python 环境和导入入口；
- `database_url`：留空使用 `data/wow-auction.db`，生产环境可通过配置传入 MySQL；
- `clear_source_after_import`：仅最后角色正常退出且全链路成功后清空 WoW SavedVariables。

插件 2.6 起使用角色级 SavedVariables。多角色模式会在每个中间角色回到选角页后立即归档、入库，但不清游戏源文件；最后角色正常退出进程后，runner 才按各自 SHA 清空本轮已经成功入库的全部角色文件。数据库按快照 SHA 和 scan fingerprint 幂等去重，重复执行不会重复插入。`source` 留空时 runner 会同时发现角色级路径，并兼容一次旧版账号级文件迁移。

## 模板回退

`ocr.enabled: false` 时才使用 NCC 模板回退。占位 PNG 不能证明任何真实界面状态，必须替换成目标机器上的实机截图。推荐保持 OCR 开启。

## 验证

```powershell
go test ./...
go vet ./...
$env:WOW_SNAPSHOT_TEST_FILE='C:\path\to\AuctionSearchExample.lua'
go test -run TestValidateExternalSnapshot -v ./internal/snapshot
```

详细状态设计见 [DEVELOPMENT_PLAN.md](../DEVELOPMENT_PLAN.md)，异常与恢复见 [FLOW_AND_EXCEPTIONS.md](../FLOW_AND_EXCEPTIONS.md)。
