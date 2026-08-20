# wow-runner 日志规范

runner 向 stderr 输出一行一个 JSON 对象（NDJSON）。日志不记录账号密码、验证码、完整 OCR 文本或拍卖数据正文。

## 公共字段

- `ts`：RFC3339 时间。
- `level`：`INFO`、`WARN`、`ERROR`。
- `event`：稳定事件名。
- `message`：简短说明。
- `run_id`：本次运行标识。
- `version`：runner 版本。
- `char_index`、`slot`、`state`：有上下文时提供。

## 状态迁移

`event=transition` 应包含：

- `from_state`
- `to_state`
- `trigger`

主要状态为：

- `INIT`
- `BNET_START`
- `WOW_FOREGROUND`
- `CHAR_SELECT`
- `ENTER_WORLD`
- `AH_PREP`
- `AH_OPEN`
- `WAIT_PLUGIN_SCAN`
- `GRACEFUL_EXIT`
- `SNAPSHOT_VALIDATE`
- `DONE`
- `FAILED`

## 关键事件

- `process_start`：启动 Battle.net。
- `process_poll`：查询进程或等待进程结束。
- `window_activate`：聚焦并验证窗口。
- `ocr_click`：OCR 找到标签并点击；记录标签和坐标，不记录整页文字。
- `ocr_state`：插件 OCR 阶段、稳定次数、是否见过 scanning。
- `input_key`：发送配置键或组合键。
- `input_command`：发送 WoW 斜杠命令。
- `scan_trigger_recorded`：记录 `scan_trigger_ts`。
- `wait_start`、`wait_satisfied`：门禁开始和成功。
- `snapshot_validated`：正常退出后的最终磁盘门禁通过。
- `failure_capture`：调试截图路径。
- `exception`：异常摘要。
- `session_start`、`session_end`：运行边界。

## snapshot_validated 字段

- `source`
- `destination`
- `last_scan_time`
- `item_count`
- `record_count`
- `actual_item_count`
- `linked_item_count`
- `incomplete_info_count`
- `missing_core_count`
- `api_error_count`

只有此事件出现且随后进入 `DONE`，才能把本轮视为数据采集成功。OCR 的 `AS_COMPLETE` 不是最终成功条件。

## 级别约定

- `INFO`：正常状态迁移、OCR 点击、完整快照。
- `WARN`：`AS_WARNING`、缺少物品链接、物品详情尚未就绪、可恢复的 OCR 瞬时错误。
- `ERROR`：维护阻断以外的不可恢复失败、自然退出超时、核心数据不完整、快照结构错误。

## 隐私与体积

- 不输出拍卖记录正文或 SavedVariables 全文。
- 不输出 OCR 捕获的整页文字。
- 失败截图只在配置了 `debug.failure_capture_dir` 时写入本机，并已由仓库忽略。
- 高频轮询仅在阶段变化、首轮或固定间隔记录，避免刷屏。
