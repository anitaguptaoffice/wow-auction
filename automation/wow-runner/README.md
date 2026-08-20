# wow-runner

Windows 自动采集链路：启动或复用 Battle.net → OCR 选择《魔兽世界》并点击「进入游戏」→ 选角 → 等待插件进入世界 → 定位拍卖师并交互 → OCR 观察扫描状态 → 正常退出 → 校验并原子同步 SavedVariables。

## 安全与成功条件

- 不自动登录账号，不输入账号、密码或验证码。
- 不调用 `TerminateProcess`。中间角色使用 `/logout`，最后一角及超时重试使用 `/quit`；自然退出超时会报错并保留客户端。
- OCR 看到完成只代表插件已在内存中生成快照，不代表文件已落盘。
- 最终成功必须在 WoW 正常退出后同时满足：
  - 最新扫描时间不早于本轮打开拍卖行的时间；
  - `itemCount == recordCount == items` 实际记录数，且大于 0；
  - `missingCoreCount == 0`、`apiErrorCount == 0`；
  - 校验通过后才原子替换目标 `data/auction.lua`。
- `linkedItemCount` 不足或 `incompleteInfoCount > 0` 只记警告；价格、数量等核心拍卖行记录仍须完整。

## 使用

```powershell
Copy-Item config.example.yaml config.yaml
# 编辑 config.yaml 后先做只读预检
go run ./cmd/wow-runner -config config.yaml -check
# 执行完整采集流程
go run ./cmd/wow-runner -config config.yaml -run
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

runner 只接受先观察到 `AS_SCANNING`、再连续稳定读到 `AS_COMPLETE` 或 `AS_WARNING` 的单调序列，避免把上一次残留的完成面板误判为本轮成功。`AS_WARNING` 仍会正常退出，最终由磁盘快照门禁决定成功或失败。

Battle.net 同样由 OCR 定位“魔兽世界”和“进入游戏”；若当前时间落在可识别的维护公告窗口内，runner 会安全停止，不点击进入游戏。`cmd/wow-ocr` 可只读截取指定窗口并输出 OCR 文本，便于校准：

```powershell
go run ./cmd/wow-ocr -exe Battle.net.exe -language zh-Hans-CN
```

## 角色与拍卖行

- `characters.mode: current`：保持选角页当前角色；适合单角色稳定采集。
- `single`：按 Home 后根据 `indices[0]` 向下定位。
- `all`：按 `indices` 顺序逐个角色运行；中间角色正常 `/logout` 回选角页。
- `keys.auctioneer_target` 非空时发送 `/targetexact <名称>`；否则使用 `auction_tar_macro`。
- `interact_target` 支持单键和组合键，如 `ALT-CTRL-H`。

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
