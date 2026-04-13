# Windows：打开客户端 → 登录 → 拍卖行 → 退出

你在 macOS 上那套「RPA 式」流程，在 Windows 上最常见的做法是 **AutoHotkey (AHK)** 或 **Python + PyAutoGUI / pywinauto**，再配合 **窗口标题 / 进程名** 把按键送到魔兽世界前台。

## 技术选型（常用）

| 方式 | 说明 |
|------|------|
| **AutoHotkey v1.1 / v2** | Windows 上键鼠脚本事实标准；社区 WoW 相关脚本多，易搜到范例。 |
| **Python + pyautogui / keyboard** | 适合与你们已有 Python 后端同一仓库维护；需处理管理员权限、焦点。 |
| **Power Automate Desktop** | 微软免费 RPA，可视化拖拽，适合非程序员维护简单流程。 |
| **图像识别（Sikuli、OpenCV）** | 按钮位置随分辨率变时用；复杂度高，一般作兜底。 |

## 可参考的开源 / 示例仓库（仅作思路，请自行审代码与协议）

以下与 **「点战网、定时、按键」** 或 **「AHK + WoW」** 相关，**不等于**官方推荐或合规背书；账号风险自负。

- **战网 / 启动器层面（偏「到点启动游戏」）**  
  - [lserman/wow-autoqueuer](https://github.com/lserman/wow-autoqueuer) — 定时点 Battle.net 的 PLAY 等（示例较老，需按当前客户端改选择器）。

- **AutoHotkey + WoW 脚本合集（偏键位、宏、窗口）**  
  - [SiderealDay/AutoHotKey](https://github.com/SiderealDay/AutoHotKey) — AHK 脚本集合，含 WoW 相关说明。  
  - [Kjella6/WoWAutohotkey](https://github.com/Kjella6/WoWAutohotkey) — 各类 WoW 用 AHK 片段。

- **更重的自动化（练级、宠物战等，仅作「AHK 能驱动一整套流程」的参考）**  
  - [FrenchToucan/AFK-WoW-Leveling](https://github.com/FrenchToucan/AFK-WoW-Leveling) — 体量大、争议也大，**不要照搬**，只说明 AHK 能搭长流程。

- **通用引擎**  
  - [AutoHotkey/AutoHotkey](https://github.com/AutoHotkey/AutoHotkey) — 官方仓库与文档。

## 与你当前需求的对应关系

1. **启动**：`Run` 战网 exe、或 `Run` `Wow.exe` / 战网 URI（视你安装路径而定）。  
2. **登录**：多数仓库**不会**安全地处理账号密码；常见是**手动登录到角色界面**，脚本只负责**进世界之后**的延时与按键。  
3. **拍卖行**：用 **KEYSTROKE** 发游戏内已绑定的宏（与 NPC 对话、打开 AH），或 `Send` 到前台 WoW 窗口；**插件**在 `AUCTION_HOUSE_SHOW` / replicate 里扫数据。  
4. **退出**：`Send` Esc / 打开菜单 / 绑定 `/logout`，等 SavedVariables 落盘后再关进程（延时需实测）。  

## 本仓库内的最小模板

- **AutoHotkey**：[example.ahk](example.ahk)（若你方仍允许 AHK 时再使用）。
- **Go（Windows SendInput 等）**：见 [GO_INPUT_REFS.md](GO_INPUT_REFS.md)（第三方库与官方 API 索引，无内置二进制）。
