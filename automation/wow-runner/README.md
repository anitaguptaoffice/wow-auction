# wow-runner

Windows 用 Go 主控：战网 → 魔兽 → 多角色拍卖扫描 → 插件小退，约定见上级目录文档。

- **开发计划**：[../DEVELOPMENT_PLAN.md](../DEVELOPMENT_PLAN.md)
- **流程与异常**：[../FLOW_AND_EXCEPTIONS.md](../FLOW_AND_EXCEPTIONS.md)
- **日志规范**：[../LOGGING_SPEC.md](../LOGGING_SPEC.md)

## 当前状态

完整 FSM：**选角前强校验**（`char_select_screen`）→ 进世界（`enter_world_actionbar`）→ 拍卖与插件登出链 → 多角循环与 **`ROUND_DONE` 杀进程**；战网点「进入游戏」前可选 **就绪模板**；模板默认占位 PNG，实机需替换并校准 ROI。

## 使用

```bash
cp config.example.yaml config.yaml
# 编辑 config.yaml 后（在 Windows 目标机上）：
go run ./cmd/wow-runner -config config.yaml
```

- `-version`：打印版本占位。
- `-check`：加载配置后查询 **战网 / Wow** 进程是否存活，向 stderr 输出 **NDJSON**（`process_poll`），用于预检。
- `-run`：**仅 Windows**：全流程自动化（见上）。`debug.failure_capture_dir` 非空时，多处失败会落盘 **PNG 截图**。macOS/Linux 上会打日志并跳过。

## 已实现

- YAML：`config.DefaultPlaceholderTemplate`、**NCC 模板**（`vision.match_method: ncc`，等价 OpenCV TM_CCOEFF_NORMED 映射到 [0,1]）、可选 **`color_gate_max_avg_channel_diff`**。
- **战网**：`bnet.ready_template`、`bnet.search_roi`、`timeouts_seconds.bnet_ui_ready`。
- **失败截图**：`debug.failure_capture_dir`（相对配置文件目录，仓库默认 `captures/` 已 gitignore）。
- **进程**、**重试**、**多角**、**ROUND_DONE** 等见代码与 `config.example.yaml`。

## 待实现（见 `../DEVELOPMENT_PLAN.md`）

更细的战网异常恢复、HSV/更复杂颜色策略等（当前已有 RGB 门控）。

## 布局

```
cmd/wow-runner/    # main
internal/config/   # YAML 解析与校验
internal/vision/   # 截图与 NCC / rgb_mean 匹配
config.example.yaml
```
