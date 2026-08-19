# 🏰 艾泽拉斯交易所 (Wow Auction)

魔兽世界拍卖行数据采集、存储与查询系统 —— 从游戏内自动扫描拍卖行数据，到后端解析缓存，再到前端可视化展示的完整数据管线。

## ✨ 功能特性

- 🎮 **游戏内自动扫描** — WoW 插件自动采集拍卖行数据，分批处理避免卡顿
- 📈 **历史价格趋势** — 插件按次扫描落盘，后端聚合时间序列，前端折线图展示（可选按完整 `itemLink` 过滤词缀）
- 📊 **实时数据缓存** — 后台每 10 秒检测数据变化，自动更新内存缓存
- 🔐 **用户认证系统** — JWT Token + bcrypt 密码哈希，安全可靠
- 🌓 **深色/浅色主题** — 魔兽风格 UI，支持主题切换与持久化
- 🎨 **物品稀有度颜色** — 完整还原游戏内物品品质颜色系统
- ⚡ **API 限流保护** — 基于 IP 的请求频率限制，防止滥用

## 🏗️ 系统架构

```
WoW 游戏客户端
  └── game/addons/AuctionSearchExample 插件
      └── 扫描拍卖行 → 导出 SavedVariables，复制为仓库 data/auction.lua

后端服务 (FastAPI，目录 backend/)
  └── app/services/auction_cache.py → 后台线程每 10 秒检查 auction.lua；快照 + 历史序列聚合
  └── app/main.py → /query、/query/history（需认证）、/register、/login 等 HTTP API

前端 (静态 SPA，目录 frontend/，可与后端分开部署)
  └── index.html + css/ + js/ → 展示与交互；js/config.js 中配置 apiBaseUrl 指向后端
```

## 📁 项目结构

```
wow-auction/
├── backend/                       # 后端（Python 包，导入名 app）
│   ├── run_dev.py                 # 本地 HTTP 开发启动
│   └── app/
│       ├── main.py                # FastAPI 应用与路由
│       ├── auth.py
│       ├── config.py
│       ├── database.py
│       ├── models.py
│       └── services/
│           ├── auction_cache.py
│           └── auction_labels.py   # timeLeftBand → 中文档位说明等
├── frontend/                      # 前端静态资源
│   ├── index.html
│   ├── css/styles.css
│   └── js/config.js, app.js
├── game/                          # 游戏侧：插件与相关脚本、参考数据
│   ├── addons/AuctionSearchExample/
│   ├── scripts/sync_raidbots.py   # Raidbots 静态数据同步
│   ├── scripts/sync_auction_lua.py # SavedVariables → data/auction.lua
│   └── data/bonus.json            # Bonus ID 等参考数据（可选）
├── automation/                    # Windows 外部自动化（Go：wow-runner）与开发计划文档
│   └── wow-runner/
├── docker/Dockerfile              # 容器构建
├── data/                          # 运行期数据（auction.lua、SQLite、raidbots 下载缓存等）
├── pyproject.toml, uv.lock        # Python 依赖（根目录仅保留必要清单）
└── README.md
```

## 🚀 快速开始

### 环境要求

- Python >= 3.13
- [uv](https://github.com/astral-sh/uv) 包管理器

### 本地运行

```bash
# 1. 克隆仓库
git clone https://github.com/your-username/wow-auction.git
cd wow-auction

# 2. 安装依赖
uv sync

# 3. 设置环境变量（可选，生产环境必须设置）
export SECRET_KEY="your-secure-secret-key"

# 4. 启动服务
uv run python backend/run_dev.py
```

服务将在 `http://0.0.0.0:8000` 启动。

### Docker 运行

```bash
# 构建镜像（构建上下文为仓库根目录）
docker build -f docker/Dockerfile -t wow-auction .

# 运行容器（镜像内为 HTTP :8000，生产环境建议前置反向代理做 TLS）
docker run -p 8000:8000 wow-auction
```

## 📡 API 接口

| 端点 | 方法 | 限流 | 说明 |
|------|------|------|------|
| `/register` | POST | 5 次/分钟 | 用户注册（默认 10 次查询额度） |
| `/login` | POST | 5 次/分钟 | 用户登录，返回 JWT Token |
| `/query?itemID=xxx` | GET | 20 次/分钟 | **当前快照**（最新日最新 scan）：含 `minBid` 起拍、`buyoutAmount`、`bidAmount`、`itemLink`、`timeLeftBand`（枚举）及 `timeLeftLabel`（中文档位说明） |
| `/query/history?itemID=&days=&itemLink=` | GET | 20 次/分钟 | 历史序列：每点含 `timestamp`、`buyoutAmount`、`minBid`、`bidAmount`、`timeLeftBand`、`timeLeftLabel`、`itemLink` 等；`days` 1–90，`itemLink` 可选 |
| `/users/me` | GET | 20 次/分钟 | 获取当前用户信息（需认证） |

### 示例请求

```bash
# 注册
curl -X POST http://localhost:8000/register \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -d "username=player1&password=mypassword"

# 登录
curl -X POST http://localhost:8000/login \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -d "username=player1&password=mypassword"

# 查询物品 (携带 Token)
curl http://localhost:8000/query?itemID=12345 \
  -H "Authorization: Bearer <your-jwt-token>"

# 历史价格序列（用于趋势图；会消耗与 /query 相同的额度）
curl "http://localhost:8000/query/history?itemID=12345&days=7" \
  -H "Authorization: Bearer <your-jwt-token>"
```

## 🎮 游戏内插件使用

### 安装

将 `game/addons/AuctionSearchExample/` 复制到 WoW 插件目录（保持文件夹名为 `AuctionSearchExample`）：

```
World of Warcraft/_retail_/Interface/AddOns/AuctionSearchExample/
```

### 斜杠命令

| 命令 | 功能 |
|------|------|
| `/as stats` | 显示数据库统计信息 |
| `/as history <物品ID>` | 查看物品拍卖历史 |
| `/as test [物品ID]` | 调试：`C_Item` 与当前 replicate 中的 `itemLink` |
| `/as clear` | 清空所有扫描数据 |
| `/as uitest [phase]` | 调试自动化状态面板（`idle` / `started` / `scanning` / `complete`） |

### 工作流程

1. 进入游戏，打开拍卖行
2. 插件自动开始扫描（大量物品会自动分批处理）
3. 退出游戏或执行 `/reload` 后，数据写入 `WTF/Account/<账号>/SavedVariables/AuctionSearchExample.lua`（文件内变量名为 `AuctionSearchDB`）
4. 将该文件同步到项目的 `data/auction.lua`（可用脚本一键复制，见下）
5. 后端自动检测 `data/auction.lua` 变化并更新缓存

**一键同步到仓库（推荐）**（在仓库根目录执行）：

```bash
# 自动在常见安装路径下查找最新的 AuctionSearchExample.lua 并复制到 data/auction.lua
uv run python game/scripts/sync_auction_lua.py

# 仅列出本机找到的候选文件（多账号时便于确认路径）
uv run python game/scripts/sync_auction_lua.py --list

# 手动指定源文件或零售根目录（见脚本文件头说明）
# WOW_RETAIL_ROOT="D:/Games/World of Warcraft" uv run python game/scripts/sync_auction_lua.py
```


## 🛠️ 技术栈

| 层级 | 技术 |
|------|------|
| 游戏插件 | Lua（`AuctionSearchExample.toc` 中 `## Interface:` 随版本更新） |
| 后端框架 | FastAPI + Uvicorn |
| 数据库 | SQLite + SQLAlchemy |
| 认证 | JWT (python-jose) + bcrypt |
| 数据解析 | slpp (Lua → Python) |
| API 限流 | SlowAPI |
| 前端 | HTML + Tailwind CSS + 原生 JS |
| 部署 | Docker / GitHub Pages (前端) |

## 📦 Raidbots 数据同步

项目包含一个数据同步脚本，从 [Raidbots](https://www.raidbots.com/developers) 下载最新的游戏静态数据（物品信息、Bonus ID 映射、副本数据等），用于将拍卖行中的物品 ID 翻译为人类可读的信息。

```bash
# 查看可同步的文件列表和状态
python game/scripts/sync_raidbots.py --list

# 一次性同步所有数据
python game/scripts/sync_raidbots.py

# 只同步核心数据（物品/词缀/图标）
python game/scripts/sync_raidbots.py --priority core

# 强制重新下载（忽略缓存）
python game/scripts/sync_raidbots.py --force

# 定时同步（每 6 小时自动检查更新）
python game/scripts/sync_raidbots.py --schedule
```

同步的数据文件按优先级分为三级：

| 优先级 | 文件 | 用途 |
|--------|------|------|
| ★★★ 核心 | `equippable-items.json`, `item-names.json`, `bonuses.json`, `icon-lookup.json` | 物品展示必备 |
| ★★☆ 重要 | `enchantments.json`, `gems.json`, `instances.json`, `crafting.json` 等 | 增强数据丰富度 |
| ★☆☆ 可选 | `encounter-items.json`, `seasons.json` 等 | 辅助功能 |

数据保存在 `data/raidbots/` 目录下（已加入 `.gitignore`）。

## 📄 License

MIT
