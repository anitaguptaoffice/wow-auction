; 最小示例：AutoHotkey v1.1
; 用途：激活魔兽世界窗口 → 等待 → 向游戏发送一串按键（请改成你的宏/快捷键）
; 运行前：以普通权限或管理员权限试运行；魔兽世界需为前台。
;
; 风险自负；仅内部调研。

#NoEnv
#SingleInstance Force
SetWorkingDir %A_ScriptDir%

wowExe := "ahk_exe Wow.exe"
delayAfterActivateMs := 5000
delayAfterSendMs := 120000

; 若魔兽世界已在前台，可注释掉下面 WinActivate 块，改用手动切窗后再按热键触发。

IfWinExist, %wowExe%
{
    WinActivate, %wowExe%
    WinWaitActive, %wowExe%, , 5
}
else
{
    MsgBox, 0, wow-ah-auto, 未找到 Wow.exe 窗口。请先启动游戏并进入世界。, 5
    ExitApp
}

Sleep, %delayAfterActivateMs%

; TODO: 改成你的「与拍卖行 NPC 对话」或「打开拍卖行」的按键 / 宏
; 示例：向当前焦点窗口发送数字键 1（动作条）；请替换为实际需要
Send, 1

; 等待插件完成 replicate（毫秒；按服务器拍卖行体量调大）
Sleep, %delayAfterSendMs%

; TODO: 退出游戏或发送 /logout —— 按你键位修改
; Send, {Esc}

MsgBox, 0, wow-ah-auto, 示例流程结束。若需退出游戏请自行编辑脚本。, 3
ExitApp
