package runner

import "errors"

// ErrPluginTimeout 表示插件状态在总死线前未形成“扫描中 → 已保存”的单调序列。
// 此错误不会直接终止 Wow；正常退出优先，避免丢失 SavedVariables。
var ErrPluginTimeout = errors.New("plugin scan timeout")

// ErrServerMaintenance is returned when Battle.net announces a maintenance
// interval that contains the current local time.
var ErrServerMaintenance = errors.New("World of Warcraft server maintenance")
