# Go 在 Windows 上模拟键鼠（自用自动化参考）

## 预期管理

- 不用 AHK、改用 **自编译 Go 程序**，可以避免 **AHK 解释器/特征** 被部分启发式标记，但底层往往仍是 **`SendInput` / `keybd_event` 同一类 Win32 调用**，反作弊若按**行为**或**驱动层**检测，**与是否 Go 无关**。
- 游戏若用 **DirectInput / Raw Input**，有时要发 **扫描码（scan code）** 而不只是虚拟键；见下方 Stack Overflow 讨论。

## 官方 API（自己用 `syscall` / `golang.org/x/sys/windows` 封装）

- [SendInput - Win32](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-sendinput) — 现代推荐入口。
- [keybd_event 已过时](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-keybd_event) — 仍有人用，不推荐新代码长期依赖。

## Go 生态里常见的封装（按需选型，自行审计）

| 项目 | 说明 |
|------|------|
| [micmonay/keybd_event](https://github.com/micmonay/keybd_event) | 跨平台键盘模拟；Windows 走底层调用，API 简单。 |
| [myfreeer/sendinput](https://github.com/myfreeer/sendinput) | 专注 Windows 的 SendInput 封装。 |
| [stephen-fox/user32util](https://pkg.go.dev/github.com/stephen-fox/user32util) | user32 辅助，含 SendInput 等。 |
| [yunginnanet/sendkeys](https://github.com/yunginnanet/sendkeys) | 在 keybd_event 之上发字符串，偏高层。 |

## 游戏/前台焦点相关

- 先 **FindWindow / GetForegroundWindow** 把目标设为前台，再 SendInput（权限、UAC、管理员进程与 UIPI 可能拦注入，需同权限或调整）。
- DirectInput 游戏：参考 [Stack Overflow: SendInput + DirectInput](https://stackoverflow.com/questions/3644881/simulating-keyboard-with-sendinput-api-in-directinput-applications)（扫描码、按下/抬起成对发送等）。

## 与本项目的关系

此处仅作文档参考；**不在本仓库提交可执行体或完整自动化逻辑**，由你方在独立模块中维护。
