# Snapshot retention implementation

- Updated requirement (2026-10-30): retain at most 14 days, keeping at least one
  snapshot for each auction market and local date that has eligible data. A global
  soft cap defaults to 200; below it all eligible observations remain. Daily floors
  take precedence when they exceed the cap. Missing dates
  are not synthesized. This supersedes the original seven-day proposal.
- Implemented: configurable selection, compact immutable generations, shared
  sync lease, durable worker recovery, empty datasets, expired-upload rejection,
  managed artifact reclamation and an opt-in systemd timer.
- Configuration, installation and operational boundaries: [DATA_RETENTION.md](DATA_RETENTION.md).

## Original operational requirements

- Expiration uses actual capture timestamps and a rolling 14-day window.
- Even a realm's sole/latest snapshot expires; an empty dataset remains healthy.
- 清理与同步导入、数据切换共用互斥机制；忙时跳过或延后，不能打断正在运行的同步或查询。
- 沿用不可变候选库、校验和蓝绿切换流程；失败继续服务旧版本，不直接改写活动数据库。
- 同时制定过期上传文件、候选库、历史数据版本及回滚副本的回收规则，保护活动版本和任务引用，确保实际磁盘占用下降，而非只隐藏查询结果。
- 保留必要的幂等回执，防止清理后重复导入；同步入口也需处理超过保留窗口的旧快照，避免重新灌回过期数据。
- 提供清理预览、运行日志和结果统计；明确调度频率、回滚保留期限及允许的清理延迟。

## 验收

- Cover the exact 14-day boundary, realm/date coverage, shared markets, all-expired
  datasets, concurrent sync, failures and restart recovery.
- 清理后最新行情、有效历史、服务器目录、快照链接和分页行为一致；过期链接有明确提示。
- 在真实数据上核对清理前后快照数、数据库大小及磁盘空间，确认调度可重复运行、无新过期数据时无需重建发布。
