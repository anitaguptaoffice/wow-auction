# wow-runner 日志与状态记录规范（端到端测试用）

本文约定 **Go `automation/wow-runner/`** 在全流程中如何打日志、记状态、报错误，便于 **按时间线对照** 实机现象、插件输出与截图。**实现须遵守**；插件侧建议对齐字段名（见 §7）。

---

## 1. 目标

| 目标 | 说明 |
|------|------|
| **可追溯** | 任意一次运行可用日志还原：何时处于何状态、因何迁移、重试了几次、是否杀进程。 |
| **可检索** | 默认 **一行一条 JSON**（JSON Lines），便于 `jq`、日志平台、简单 `grep`。 |
| **可对齐** | 与 [FLOW_AND_EXCEPTIONS.md](FLOW_AND_EXCEPTIONS.md) 中 **E1–E13**、**§5 核心异常** 可一一对应。 |
| **不吞错** | 所有 `error` 返回值、恢复分支、`recover`（若有）**必须**落日志；静默重试禁止。 |

---

## 2. 输出与格式

| 项 | 约定 |
|----|------|
| 编码 | **UTF-8** |
| 形态 | **NDJSON**：每行一个 JSON 对象，行尾 `\n` |
| 时间 | 字段 **`ts`**：**RFC3339**，建议带纳秒，如 `2026-04-07T12:34:56.789012345+08:00` |
| 默认输出 | **stderr**（避免与工具 stdout 管道混淆）；可选 **`--log-file path`** 写文件且仍镜像 stderr，或仅写文件（实现时二选一并在 `--help` 说明） |
| 人类可读 | 可选：同时输出简化单行文本；**机器解析以 NDJSON 为准** |

---

## 3. 每条日志的公共字段（信封）

以下字段**尽量出现在每一条**日志中（`DEBUG` 级可省略部分非关键字段，但 `run_id` / `state` 建议保留）。

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `ts` | string | 是 | 见 §2 |
| `level` | string | 是 | `DEBUG` / `INFO` / `WARN` / `ERROR` |
| `event` | string | 是 | 事件类型，见 §5 |
| `run_id` | string | 是 | 单次进程启动生成的 UUID，便于多轮测试区分 |
| `component` | string | 是 | 固定 `wow-runner` |
| `version` | string | 建议 | 构建版本或 `dev` |
| `state` | string | 建议 | 当前 FSM 状态 ID，与 [DEVELOPMENT_PLAN.md §4](DEVELOPMENT_PLAN.md) 一致 |
| `char_index` | int \| null | 建议 | 当前角色下标（无角色上下文时为 `null`） |
| `char_total` | int \| omit | 可选 | 配置中角色总数，便于读日志 |
| `attempt` | int \| omit | 可选 | 当前状态或当前角内的尝试序号（从 1 开始） |
| `pid_bnet` | int \| null | 可选 | 战网进程 PID，未知为 `null` |
| `pid_wow` | int \| null | 可选 | `Wow.exe` PID |
| `msg` | string | 建议 | 人类可读短句 |
| `error` | object \| omit | 见 §6 | 仅当本事件为错误或携带原因时 |

---

## 4. 计数器与全局熔断（必须在日志中可见）

下列数值**任一变更为何**，须打 **`event: "metric"`** 或等价字段，避免只在内存里变：

| 指标 | 字段名 | 说明 |
|------|--------|------|
| 当前角累计失败（可恢复路径） | `retry_char` 或分状态 `retry_*` | 与配置 `max_retries_per_character` 对照 |
| 全流程因异常杀 `Wow` 次数 | `kill_wow_total` | 与 `max_kill_restart_total` 对照 |
| 当前状态已重试次数 | `retry_in_state` | 单状态内 Esc/重试 |
| 视觉轮询次数 | `poll_count` | 可选，或仅在 `DEBUG` 每 N 次一条 |
| 扫描触发时刻 | `scan_trigger_ts` | **RFC3339**，与 [FLOW_AND_EXCEPTIONS.md §2.6](FLOW_AND_EXCEPTIONS.md) 一致；**每次更新须打日志** |

进入 **`FAILED`** 退出前，必须有一条 **`level":"ERROR"`** 且含 **`exit_code`** 与 **`reason` 枚举**（见 §8）。

---

## 5. 事件类型 `event`（全量清单）

实现时**不得省略**下表中与当前行为对应的类型；若合并多条为一条，须在同一 JSON 内用嵌套对象区分（不推荐随意合并）。

### 5.1 会话与配置

| `event` | 说明 | 建议附加字段 |
|---------|------|----------------|
| `session_start` | 进程启动、解析 CLI | `argv`（脱敏）、`config_path` |
| `config_loaded` | 配置校验通过 | `config_hash`（可选，配置摘要） |
| `config_error` | 配置缺失/非法 | `error`（§6），**不继续主流程** |
| `session_end` | 正常或异常退出前最后一条 | `exit_code`、`outcome`：`success` \| `failed` \| `interrupted` |

### 5.2 状态机（FSM）

与 [DEVELOPMENT_PLAN.md §4](DEVELOPMENT_PLAN.md) 状态 ID **字符串完全一致**。

| `event` | 说明 | 建议附加字段 |
|---------|------|----------------|
| `state_enter` | 进入某状态 | `from_state`、`to_state` |
| `state_exit` | 离开某状态 | `from_state`、`to_state`、`reason`：`success` \| `timeout` \| `retry` \| `abort` |
| `transition` | 一次明确迁移（可与 enter/exit 二选一，但**禁止三者全无**） | `from_state`、`to_state`、`trigger` |

`trigger` 示例：`template_matched`、`process_found`、`timeout`、`retry_exhausted`、`user_signal`、`kill_restart`。

### 5.3 进程与窗口

| `event` | 说明 | 建议附加字段 |
|---------|------|----------------|
| `process_poll` | 周期性或关键点查询进程 | `name`、`found`、`pid` |
| `process_start` | 启动外部进程 | `exe_path`、`ok`、`error?` |
| `process_kill` | 结束进程 | `target`、`pid`、`reason`（如 `PLUGIN_STUCK`、`ROUND_DONE`、`E9`） |
| `window_activate` | 前台/聚焦 | `title_hint`、`hwnd`、`ok`、`error?` |

### 5.4 输入

| `event` | 说明 | 建议附加字段 |
|---------|------|----------------|
| `input_key` | 键盘 | `keys`（符号名数组，如 `["Enter"]`）、`modifiers` |
| `input_mouse` | 鼠标点击 | `x`、`y`、`button` |
| `input_combo` | 组合键如 Alt+F4 | `description` |

**不要**在日志中记录密码；战本路径若含敏感目录，可截断或哈希。

### 5.5 视觉 / OCR

| `event` | 说明 | 建议附加字段 |
|---------|------|----------------|
| `capture` | 截图成功/失败 | `region_id`、`width`、`height`、`duration_ms` |
| `ocr` | 一次 OCR 结果（`INFO` 可摘要，`DEBUG` 可全文） | `region_id`、`text_sample`、`matched`、`confidence`（若有） |
| `template` | 模板匹配 | `template_id`、`score`、`matched` |
| `visual_decision` | 综合判定结果 | `decision`：`bnet_launch` \| `char_screen` \| `in_world` \| `ah_open` \| `unknown` 等 |

轮询时：**至少**在判据变化、`INFO` 级状态迁移、或超时时打 `ocr`/`visual_decision`；`DEBUG` 可每次轮询一条（可配置 `--verbose`）。

### 5.6 等待与超时

| `event` | 说明 | 建议附加字段 |
|---------|------|----------------|
| `wait_start` | 开始等待某判据 | `wait_id`、`timeout_ms`、`deadline_ts` |
| `scan_trigger_recorded` | **`AH_OPEN` 成功**（**tar 宏 + 交互** 已开拍卖；插件自动出 UI 并扫），**开始总计时**（见 [FLOW §2.6](FLOW_AND_EXCEPTIONS.md)）；**插件 UI 扫完模板** 见 [FLOW §2.4](FLOW_AND_EXCEPTIONS.md) | **`scan_trigger_ts`**（RFC3339）、`char_index` |
| `wait_satisfied` | 判据满足 | `wait_id`、`elapsed_ms`；若在 `WAIT_PLUGIN_LOGOUT` 成功，建议带 **`since_scan_trigger_ms`** |
| `wait_timeout` | 判据未在时限内满足 | `wait_id`、`elapsed_ms`、`next_action`；`PLUGIN_STUCK` 时须含 **`scan_trigger_ts`** |

### 5.7 异常与恢复（对齐 FLOW 文档）

| `event` | 说明 | 建议附加字段 |
|---------|------|----------------|
| `exception` | 映射到 E1–E13 或内部码 | `code`（见 §7）、`recover_action` |
| `pipeline_restart` | 从战网或等价入口整流程重跑 | `same_char_index`、`kill_wow_total` |
| `fallback` | 插件 API 不可用走键序等 | `fallback_id` |

---

## 6. 错误对象 `error`（结构化）

当 `level` 为 `WARN`/`ERROR` 或事件含失败原因时，使用嵌套对象：

| 字段 | 类型 | 说明 |
|------|------|------|
| `code` | string | 见 §7 |
| `message` | string | 简短英文或中文均可，**稳定关键词**便于 grep |
| `cause` | string | 可选，`fmt.Errorf` 链或 `errno` |
| `stack` | string | 可选，Go `runtime.Stack` 或省略（首版） |
| `screenshot` | string | 可选，落盘截图绝对路径 |
| `fsm_state` | string | 发生时的状态 |
| `char_index` | int \| null | 重复信封便于单行解析 |

---

## 7. `code` / `reason` 枚举（与文档对齐）

| `code` | 含义 |
|--------|------|
| `E1`–`E13` | 与 [FLOW_AND_EXCEPTIONS.md §6](FLOW_AND_EXCEPTIONS.md) 一致 |
| `CONFIG` | 配置错误 |
| `PLUGIN_STUCK` | `WAIT_PLUGIN_LOGOUT` 超时，将杀进程重启 |
| `RETRY_EXHAUSTED` | 单角或单状态重试用尽 |
| `KILL_BUDGET_EXHAUSTED` | 全局杀进程次数用尽 |
| `INTERNAL` | 未分类实现错误（应极少） |

退出码仍按 [FLOW_AND_EXCEPTIONS.md §8](FLOW_AND_EXCEPTIONS.md) 约定；**最后一条** `session_end` 必须带相同 `exit_code`。

---

## 8. 插件侧建议（Lua）

便于与 Go 日志 **按时间对齐**：

- 在关键节点 `print` 一行前缀固定、含 **`run_id`**（若通过 SavedVariables 或文件与 Go 约定传递）或至少 **`ts`**（需插件侧用 `date()` 或等价）。
- 建议前缀：`[AuctionSearchExample]`，字段：`action=logout_done` 等键值对。

Go 若 **tail** 插件日志，应打 `event: "plugin_line"` 附原始行（可选功能）。

---

## 9. 端到端测试检查清单（自测）

- [ ] 单次成功跑完全程：可仅凭日志列出 **状态顺序** 与 **char_index** 变化。
- [ ] 人为制造战网广告：日志中有 **`ocr`/`visual_decision`** + **`input_combo` Alt+F4** 或等价。
- [ ] 人为卡住插件小退：日志出现 **`wait_timeout`** → **`PLUGIN_STUCK`** → **`process_kill`** → **`pipeline_restart`**，且 **`char_index` 不变**。
- [ ] 重试耗尽： **`session_end`** 的 `exit_code` 与 **`reason`** 一致。

---

**维护**：新增 FSM 状态或异常 ID 时，同步更新本文 §5、§7 与 `DEVELOPMENT_PLAN.md` §4。
