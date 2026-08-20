# CloudBase 运行手册

## 资源隔离

目标环境为 `raidbot-5gh3h2nx762bedc5`（`ap-shanghai`），它同时承载其他应用。WoW 拍卖项目只使用以下命名空间：

- CloudRun：`wow-auction-api`
- 静态托管：`/wow-auction/`
- 私有对象存储：`wow-auction/`
- MySQL 表：`wow_auction_*`
- MySQL 应用账号：`wow_auction_app@172.17.%`

不要覆盖静态托管根目录，不要修改现有 `cbg-report-http` 服务或 `simc-dist/` 存储前缀。

## 已部署环境

- 网站：<https://raidbot-5gh3h2nx762bedc5-1251932919.tcloudbaseapp.com/wow-auction/>
- API：<https://wow-auction-api-273424-4-1251932919.sh.run.tcloudbase.com>
- CloudRun：1 CPU / 2 GiB，`MinNum=0`，`MaxNum=1`
- VPC：`vpc-nj4vm40c`（`172.17.0.0/16`）
- 子网：`subnet-5kd1rnb1`（`172.17.0.0/20`）
- MySQL 私网：`172.17.0.6:3306`
- 数据库：`raidbot-5gh3h2nx762bedc5`

MySQL 公网入口保持关闭。应用账号只对该数据库拥有 `SELECT/INSERT/UPDATE/DELETE/CREATE/DROP/INDEX/ALTER/REFERENCES`；不使用 `root` 运行服务。

当前在线原始对象：

```text
wow-auction/snapshots/CN/unlabeled/2026-08-20-044100/auction.lua.tgz
wow-auction/snapshots/CN/unlabeled/2026-08-20-044100/manifest.json
wow-auction/snapshots/CN/unlabeled/latest.json
```

`unlabeled` 是有意保留：本次快照没有服务器/连接区元数据，不能凭角色名或客户端猜测。

## 当前生产快照

- 扫描时间：`2026-08-20 04:41:00 +08:00`
- Lua 大小：`173,460,603` bytes
- Lua SHA-256：`8c60305ed1c19b7031b150012b72cab809a474dc45c1d6a14857d596ce67f961`
- tgz 大小：`11,867,619` bytes
- 原始记录：`380,668`
- 基础物品：`12,759`
- 网站市场项：`13,303`
- 总数量：`42,894,416`
- 战宠：`545` 组 / `1,210` 条明细
- 核心字段、链接、单价和摘要重算差异：`0`

首次线上导入耗时 `99.21s`。相同 SHA 再次提交会安全 no-op：新增 `0` 行，返回现有 `380,668` 行。

## 用户认证

CloudBase 原生认证已启用：

- Email Login：开启
- Username Login：开启
- Anonymous Login：关闭
- Email Provider：开启，平台默认代发
- 安全域名：已包含生产静态域名

前端使用 `@cloudbase/js-sdk@3.8.0` 的本地 bundle 和 Publishable Key。Publishable Key 可以放在浏览器；Server API Key、SecretId、SecretKey、Client Secret 不得放入前端。邮箱注册、密码登录、会话恢复和退出已接入，临时用户真实登录测试完成后已删除。

物品图标源通过构建变量 `VITE_WOW_ICON_BASE_URL` 配置，默认使用 Blizzard 官方图标 CDN，不占用 TCB 静态资源和下行流量。如后续实测中国大陆访问质量不稳定，再把生成清单涉及的图标同步到 TCB 静态资源并切换该变量。

## 部署后端

仓库根 `Dockerfile` 只构建 FastAPI。`.dockerignore` 排除快照、插件、自动化、前端和 `node_modules`，构建上下文不应包含 173 MB Lua。

```powershell
tcb -y cloudrun deploy `
  -e raidbot-5gh3h2nx762bedc5 `
  --serviceName wow-auction-api `
  --port 8000 `
  --source . `
  --force --json `
  --vpcConfig '{"vpcId":"vpc-nj4vm40c","vpcCIDR":"172.17.0.0/16","subnetId":"subnet-5kd1rnb1","subnetCIDR":"172.17.0.0/20"}'
```

数据库密码和 `IMPORT_ADMIN_TOKEN` 只放 CloudRun 运行时环境变量，不写入仓库、命令历史或前端。配置更新时必须继续保留 VPC，并保持 `MaxNum=1`。

## 部署网站

```powershell
npm ci
npm run typecheck
npm run build:cloudbase
tcb hosting deploy `
  -e raidbot-5gh3h2nx762bedc5 `
  frontend/dist wow-auction `
  --json
```

`build:cloudbase` 会先从数据库重新生成完整纹理清单，再读取持久化的图标来源状态，只检测新图标、未检测图标或本地文件丢失的图标。没有 listfile 文件名的纹理会按 `FileDataID` 直接从 CASC 导出；Blizzard CDN 缺失的其他资源会从备用 JPEG 或 CASC 固化到静态产物。无法取得的纹理会回退到页面占位，不会阻断部署。

不要把 `frontend/dist` 部署到托管根目录。Vite 会为 JS/CSS 生成内容哈希文件名，HTML 保持入口文件；部署后仍需核对 `/wow-auction/` 裸路径、带 query URL 和静态资源缓存头。

## 新快照更新流程

1. 从 WoW SavedVariables 取得新的 `auction.lua`。
2. 本地计算 SHA-256，并先运行导入器做完整性验证：

   ```powershell
   $env:PYTHONPATH = "backend"
   uv run --frozen wow-auction-import data/auction.lua --json
   ```

3. 创建只包含根目录 `auction.lua` 的 tgz 和 manifest，上传到：

   ```text
   wow-auction/snapshots/<region>/<realm-or-unlabeled>/<timestamp>/
   ```

4. 将 CloudRun 临时改为 `MinNum=1, MaxNum=1`，等待新版本正常。
5. 为 tgz 生成短期私有签名 URL：

   ```powershell
   tcb storage url '<object-key>' -e raidbot-5gh3h2nx762bedc5 --expires 3600 --json
   ```

6. 用运行时保存的管理令牌调用：

   ```http
   POST /api/admin/import
   Authorization: Bearer <IMPORT_ADMIN_TOKEN>
   Content-Type: application/json

   {"sourceUrl":"<signed-url>","expectedSha256":"<lua-sha256>"}
   ```

7. 轮询 `GET /api/admin/import/{jobId}`，直到 `complete` 或 `error`。
8. 直接核对 MySQL：声明条数、实际 listings、唯一 source_index、summary、总数量、战宠分组、核心字段和单位价格必须全部闭环。
9. 更新同前缀的 `latest.json`，再把 CloudRun 恢复为 `MinNum=0, MaxNum=1`。

## 失败与恢复

- 导入使用单事务；校验或写入失败不会留下 `complete` 的半成品 scan。
- SHA 完全相同的快照不会重复写入。
- MySQL `GET_LOCK` 防止不同实例同时导入；CloudRun 仍必须 `MaxNum=1`，因为 job 状态保存在进程内存。
- 若导入时实例重启，轮询状态会丢失；先查 MySQL 是否已有完整 scan，再重新提交相同 SHA。重复提交是安全的。
- 解析 173 MB 快照峰值约 1.37 GiB；不要降低到 1 GiB，也不要在同一实例并发导入。
- 开启定时更新前，先定义历史保留数量和对象存储生命周期；当前不会自动清理历史快照。
