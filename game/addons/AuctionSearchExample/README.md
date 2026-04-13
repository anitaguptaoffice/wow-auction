# AuctionSearchExample（自动化联调）

与 `automation/wow-runner` 配合时，请在游戏内 **系统 → 插件** 勾选本插件；打开拍卖行后顶部状态条会随阶段变化，便于截取 PNG 填入 `config.yaml`。

| 阶段 (`automation_phase` 日志) | 面板内容 |
|-------------------------------|----------|
| `started` | 单行：`[扫描] 已开始扫描` |
| `scanning` | 单行：`[扫描] 扫描中` |
| `complete` | **同一屏双行**：上行 `[扫描] 扫描完成`，下行 `[登出] 请配合外部脚本返回角色` |

**`complete` 一屏即可** 同时给 wow-runner 用作「扫描完成」识别与登出链前的视觉锚点，无需再切一屏。

调试：`/auctionsearch uitest complete`（或 `uitest logout`，与 `complete` 相同）。

若插件显示「过期」，把 `AuctionSearchExample.toc` 里的 `## Interface:` 改成与你客户端一致。
