# 外部程序增量同步接口 v1

外部程序只上传一份新的完整采集快照，不需要下载或维护历史数据库。服务器增量解析、追加和验收，再切换只读 API；失败保留旧数据。这里只实现网络增量传输，服务器内部仍复制完整候选 DuckDB，以保留回滚能力。

## 直接使用

Python 3.13 环境，命令在仓库根目录执行。令牌由管理员通过独立文件交付，不放在网址、源码、日志或浏览器前端里。

```powershell
# 如果仍输出现有 Lua 或单快照 auction.lua.tgz，先转换；已有 JSONL.gz 无需此步
$env:PYTHONPATH = 'backend'
./.venv/Scripts/python.exe -X utf8 -m compact_market.export_snapshot input.lua snapshot.jsonl.gz

# 仅上传这一份文件，并等待最终发布回执
python scripts/sync-snapshot.py snapshot.jsonl.gz --server https://wowplayer.fun --token-file C:/path/to/sync.token
```

转换器一次只接受一份完整快照，不允许覆盖已有输出。Gzip 时间戳固定为 0，相同输入产生相同文件 SHA。已有 Lua 和 JSONL 的逻辑指纹一致，可跨格式去重。不要为了重试修改采集时间或重排挂单。网络中断后用同一文件重新运行：先查询相同 SHA 的已有任务，避免重复上传或导入。

## 文件格式

文件名后缀 `.jsonl.gz`，UTF-8 JSON Lines，Gzip 压缩。每行是一个 JSON 对象，最后一行也以换行结束。第一行：

```json
{"type":"snapshot","schema_version":1,"complete":true,"region_id":5,"region_name":"CN","realm_id":707,"realm_name":"白银之手","normalized_realm_name":"白银之手","captured_at":1793291872,"source_date":"2026-10-30","record_count":1}
```

其余每行是一个 `type: "auction"` 的原始挂单，保留现有客户端字段名：

| 字段 | 类型与要求 |
| --- | --- |
| `itemID`、`quantity` | 正整数 |
| `name`、`itemLink` | 非空字符串；链接必须保留 bonus、附魔、宝石等原始内容 |
| `minBid`、`buyoutAmount`、`bidAmount` | 非负整数铜币；仅竞价挂单的一口价为 0 |
| `timeLeftBand` | 整数 1–4 |
| `hasAllInfo` | 必须为 `true` |
| `marketScope` | `region`（区域共享商品）或 `realm`（本服），不能猜测或填 unknown |
| `texture`、`qualityID` | 原始 FileDataID、物品品质；允许 null，但应尽可能提供以正确显示图标及品质色 |
| `usable`、`highBidder` | 可选 bool/null |
| `level`、`minIncrement`、`saleStatus`、`battlePetCreatureID`、`battlePetDisplayID` | 可选非负整数/null |
| `levelType`、`owner`、`ownerFullName`、`bidderFullName` | 可选字符串/null |

`captured_at` 是 Unix 秒数；`source_date` 是采集客户端日期 `YYYY-MM-DD`，省略时按北京时间推导。区域/服务器 ID 为正整数，区域 0 与服务器 0 不能作为采集来源；共享市场由服务器派生。`record_count` 必须与后续挂单行数一致，0 仅用于实际完整采集为空的市场。仅上传完整快照，不能把这次新增的几条挂单伪装成完整快照；这里的增量单位是“快照”。最低价、数量、属性组合等摘要由服务器计算，客户端不提供摘要或 SQL。

限额：压缩后 128 MiB，解压后 1 GiB，单行 64 KiB，最多 100 万条挂单。接收和导入都有流式字节边界；JSONL 原始挂单可重复流式读取，避免同时保留全部原始行。摘要及字典仍需要内存。上传租约 15 分钟，文件传输最长 10 分钟；首版不支持断点续传。图标沿用已有映射和缓存，新增且未解析的图标不会阻止行情发布。

## HTTP 协议

全部接口使用 HTTPS，请求头 `Authorization: Bearer <token>`。无令牌或错误令牌返回 401；状态接口也需要鉴权。

### 1. 申请任务：POST /api/sync/jobs

`Content-Type: application/json`，请求体只含以下字段：

```json
{"schema_version":1,"compressed_sha256":"压缩文件的64位小写SHA256","size_bytes":12345,"region_id":5,"realm_id":707,"captured_at":1793291872,"record_count":1}
```

成功返回 202 和 `id`、`phase`、`upload_url`、`status_url`。同一 SHA 的相同请求返回已有任务及 200，包括已经成功的回执。相同 SHA 配不同元数据返回 422。任务明确失败后，重试会创建新任务 ID；原任务记录保留。已有其他任务或数据切换时返回 409，附 `Retry-After: 10`，此时不应开始上传文件。

### 2. 上传：PUT /api/sync/jobs/{id}/snapshot

请求体就是 Gzip 文件字节，`Content-Type: application/gzip`，`Content-Length` 必须与申请时一致。不要设置 HTTP `Content-Encoding`。大小及 SHA 验证成功后返回 202，状态 `queued`；文件流校验失败返回 422 并释放任务。已进入队列或完成的相同任务返回原状态，不再接收第二次文件。通过独立临时文件接收，不写运行中的数据库。

### 3. 查询：GET /api/sync/jobs/{id}

状态：`awaiting_upload → uploading → queued → importing → publishing → succeeded`。失败为 `failed`；发布结果无法确认时为 `publication_unknown`，保留互斥，不自动接受下一个任务。`202` 不代表已上线，只有 `succeeded` 才是最终成功。结果中的 `changed: false`、`added_scans: 0` 表示快照重复或已被更新的观测覆盖，没有切换线上数据。

任务和回执在服务器磁盘上保存，进程重启不清空。文件未传完会在租约到期后失败；导入中的 Docker 容器使用固定任务名，工作进程重启后等待原容器而不是重复启动。发布通过现有持久回执核实结果；无法确定时应由管理员检查任务、活动数据库与回滚记录，不能直接清除互斥记录。

## 部署与运维

入口是独立的无特权容器，只挂载同步收件目录及只读 Token，无 Docker socket、历史数据库或 Nginx 配置权限。受限导入容器无网络、只读根目录、2 GiB 内存、2 CPU；后台主机服务负责 Docker 调度及原子切换。生产入口只监听 127.0.0.1，由 HTTPS Nginx `/api/sync/` 转发，并关闭请求预缓冲。

使用 `Dockerfile.sync` 基于现有不可变依赖镜像构建。配置参考 `sync.example.json`。`sync_worker.py` 使用主机 Python 3.6 标准库，`sync_api.py` 使用镜像中的 Python 3.13。全局任务租约及发布锁由 HTTP、后台工作进程和更新后的命令行发布器共同检查；旧版整库发布脚本应停止使用或更新。继续使用 Lua 本地导入是兼容路径，但外部程序优先使用此接口。

保留成功发布的候选库及回滚记录；不要直接删除包含当前挂载数据库的任务目录。定期归档失败/未改变数据的收件文件，监测磁盘余量。Token 仅在服务端私密文件与客户端私密文件保存；轮换时替换服务端文件并重启入口，再更新授权客户端。
