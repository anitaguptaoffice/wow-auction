# 按服务器管理快照

公开网站保持只读。快照增删通过本地管理命令与服务器 SSH 执行，不开放匿名删除接口。数据更新可单独发布，使用当前 API 容器的同一个镜像，不夹带前端或 API 代码发布。

## 清单与身份

```powershell
$env:PYTHONPATH = 'backend'
./.venv/Scripts/python.exe -X utf8 -m compact_market.manage list --database data/compact/current.duckdb
```

输出服务器 ID、区域 ID、原始快照 `scan_id`、采集时间、记录数、指纹和源文件 SHA。管理命令中的 `source-scan` 指原始采集快照，**不能使用公开 API 派生的 `scanId`**。全服市场 `realmID=0` 是从原始快照中的共享商品计算出来的，不是可独立删除的采集服务器。

## 归档客户端文件

```powershell
./.venv/Scripts/python.exe -X utf8 -m compact_market.manage archive 'F:/World of Warcraft/_retail_/WTF/Account/ACCOUNT/白银之手/角色/SavedVariables/AuctionSearchExample.lua' --archive-root data/archive/managed
```

按源文件 SHA 存放 `auction.lua.tgz` 和 `manifest.json`。复制前后验证文件没有变化，再检查压缩包中的实际内容；不会清空、改写游戏文件。相同文件归档可重复执行。

## 添加、删除与保留

```powershell
# 给当前数据集增加快照。重复来源或指纹会跳过，不重复计数。
./.venv/Scripts/python.exe -X utf8 -m compact_market.manage stage --database data/compact/current.duckdb --output data/compact/next.duckdb --add data/archive/managed/SHA/auction.lua.tgz

# 移除指定服务器的所有历史：区域 5、服务器 741。
./.venv/Scripts/python.exe -X utf8 -m compact_market.manage stage --database data/compact/current.duckdb --output data/compact/without-realm.duckdb --remove-realm 5:741

# 只移除一份原始快照。
./.venv/Scripts/python.exe -X utf8 -m compact_market.manage stage --database data/compact/current.duckdb --output data/compact/pruned.duckdb --remove-source-scan 3

# 只保留原库中的一份，再加入三个新来源；强制最终正好四份。
./.venv/Scripts/python.exe -X utf8 -m compact_market.manage stage --database data/compact/current.duckdb --output data/compact/four.duckdb --keep-source-scan 4 --add data/archive/managed/PANDA_SHA/auction.lua.tgz --add data/archive/managed/WHITE_SHA/auction.lua.tgz --add data/archive/managed/DEATHWING_SHA/auction.lua.tgz --expect-scans 4
```

`stage` 不修改原库，也不会覆盖已有输出。未知 ID、冲突的保留/删除条件、空结果、条数不符或文件校验失败都会停止。输出新 `.duckdb` 和同名 `.report.json`，报告列出保留、移除、加入、跳过的快照与逐份校验证据。新快照完整往返校验；保留快照仅在基库 SHA 与已有报告完全吻合时继承校验，否则重新完整校验。需要全量审计时传 `--full-verify`。旧摘要直接复用，新摘要随新快照追加。旧 ID 保持稳定，已删除 ID 不分配给其他服务器；描述字典去重，删除时移除孤立记录。删除某服务器来源时，它提供的共享商品历史观测也一并退出在用数据集。

重复导入已存在的来源不会增加挂单或字典。误删后可以把归档重新 `--add`，也可以直接回滚到保留的旧数据库版本。构建失败的临时目录自动清理，原库始终可用。

## 单独切换线上数据

上传数据库、报告和源归档到服务器的新目录，保持旧数据不动。核对报告 SHA 后，在服务器执行：

```sh
python3 deploy/dataset.py switch \
  --database /opt/wow-auction/data/GENERATION/market.duckdb \
  --report /opt/wow-auction/data/GENERATION/market.report.json \
  --expect-scans 4 \
  --previous-container CURRENT_CONTAINER --container NEXT_CONTAINER \
  --previous-port 8180 --port 8181 \
  --config /etc/nginx/wow-auction-api.conf \
  --backup-dir /opt/wow-auction/backups/datasets \
  --expected-current-sha CURRENT_DATABASE_SHA256 \
  --public-url https://wowplayer.fun
```

切换脚本从旧容器取**不可变镜像 ID**，只换数据库挂载。它检查旧库 SHA，先启动候选容器，验证完整服务器/快照清单与分页挂单，再修改唯一匹配的 API upstream，执行 `nginx -t`、重载并检查公网目录。成功后停止旧容器，旧数据库和容器都保留。健康检查、配置校验或公网验证失败时恢复旧路由。不会切换网站静态目录。

输出 `rollback_record` 是本次操作的审计和回滚清单。回滚：

```sh
python3 deploy/dataset.py rollback /opt/wow-auction/backups/datasets/OPERATION.json
```

回滚会校验旧库与配置 SHA，启动旧容器并等待健康，再恢复路由。若已有更新的发布改变了配置，旧回滚清单会拒绝覆盖。重复回滚返回已恢复。主机上同一时间只允许一个数据切换；构建输出也有独占锁。若主机异常中断留下 `.lock`，先查看其中的 PID 并确认操作进程已退出，再由管理员移除该锁文件后重试。

## 验证

```powershell
./.venv/Scripts/python.exe -X utf8 -m unittest tests.test_snapshot_management tests.test_dataset_deploy tests.test_compact_market tests.test_unified_market
```

覆盖新增、重复导入、按服务器删除、按快照删除、恢复、ID 不复用、非法来源、SHA 不符、输出冲突、精确目录验证、候选失败、Nginx 失败恢复与旧回滚清单防覆盖。
