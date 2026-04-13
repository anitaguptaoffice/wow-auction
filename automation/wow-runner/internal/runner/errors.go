package runner

import "errors"

// ErrPluginTimeoutKill 表示 WAIT_PLUGIN_LOGOUT 总超时后已尝试终止 Wow，调用方应全流程重启并同角重试（见 DEVELOPMENT_PLAN §5.2）。
var ErrPluginTimeoutKill = errors.New("WAIT_PLUGIN_LOGOUT timeout, Wow.exe terminated")
