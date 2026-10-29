# 选角页原生状态只读探针

这是独立 Go 验证命令，不是自动按键服务。默认只读客户端原生数据，不执行 Lua、不调用游戏函数、不注入、不写入游戏内存。显式 `-key Up|Down` 才进行一次后台方向键实验，不发送回车。

## 已验证（2026-10-30）

客户端 Windows 国服 12.1.5.70575，原 EXE SHA-256：
`b9739df5d0c973c486b618faa50dc0d60a747441ff713cd7c46234daa447071a`。

1. 用户确认选中缚龙截江—遗忘海岸，Go 从原生选中索引和角色 GUID 表读到索引 9。
2. 用户切换到同名角色缚龙截江—熊猫酒仙，Go 读到索引 4、不同 GUID；角色名称相同未造成混淆。
3. 沿角色记录的服务器查询键查客户端原生缓存，直接读出熊猫酒仙、遗忘海岸、凤凰之神；不使用用户输入填充结果。
4. 用户进入世界后，选角标志变为 false，探针停止提供选角角色列表，selectedCharacter 为 null。
5. 用户小退后，选角标志恢复 true，重新读到熊猫酒仙的角色，可按角色名＋服务器名称唯一匹配。
6. 用户完整重启客户端后，PID 从 43600 变为 46852；重新绑定进程读到 71 个槽位、70 个非空角色，当前仍为缚龙截江—熊猫酒仙（索引 4），目标唯一匹配，两次采样一致，耗时 199 ms。本次模块基址恰好与重启前相同，因此只证明新进程重启验证通过，不作为基址实际变化的验证。
7. 用户再次重启后，PID 变为 28340，模块基址从 `0x7FF674320000` 变为 `0x7FF70D0F0000`；仍正确读到原角色、列表和唯一目标。这次覆盖了实际基址变化。
8. 显式后台 Down 实验：选中项从索引 4（缚龙截江—熊猫酒仙）变为索引 5（Kodachi—熊猫酒仙），发送前后前台 HWND 均为 458944，目标窗口为 787302。随后 Up 实验发现游戏已在前台，拒绝发送；后续恢复结果另记。

两次一致性采样约 200–280 ms，其中主动间隔 150 ms。该时间不包含读取 EXE 并核验 SHA 的初始化。一次性命令每次启动均重新核验；未来常驻服务可在绑定进程时核验。

## 运行

在 wow-runner 目录构建：

```powershell
go build -o state-probe.exe ./cmd/state-probe
./state-probe.exe -pid <当前PID> -exe 'F:/World of Warcraft/_retail_/Wow.exe' -character '缚龙截江' -realm '熊猫酒仙' -out '<不存在的本地报告文件>'
```

默认仅读取版本固定的原生状态。`-snapshot` 可显式选择同一模式。未知 EXE 哈希、不可读指针、非法计数、GUID 不一致或连续采样变化均拒绝发布。只请求 PROCESS_QUERY_INFORMATION 和 PROCESS_VM_READ。

`selectedCharacter` 表示选角页当前选中项，不表示已经登录。
`targetMatches` 同时匹配角色名和客户端返回的服务器名称；`targetUnique` 只表示身份匹配唯一。
`nativeSlotZeroBased` 是原生 GUID 数组索引，**不能直接转换成要按几次方向键**。
`realmLookupKey` 是客户端服务器缓存查询键；`guidRealmIdCandidate` 是按参考 GUID 布局提取的字段，两者不等同，不把它们混作官网服务器 ID。

## 原生证据位置（相对模块基址）

| 字段/函数 | RVA | 依据 |
|---|---|---|
| 选角标志 | 0x608136D | SetInCharacterSelect 包装函数 0xE3A4C0 的真实指令写 0/1 |
| 当前选中索引 | 0x5D6E518 | 原生 SelectCharacter 路径 0x257F390 写入；小退进入/退出流程使用 |
| 角色 GUID 数组/槽位数 | 0x823FE20 / 0x823FE28 | GetNumCharacters、GetCharacterGUID、SelectCharacter 三条路径共同引用 |
| GUID → 角色记录映射 | 0x5E0F260 / 0x5E0F268 | 原生查询函数 0x25836E0；桶数/桶指针 |
| 角色记录字段 | GUID +0；名字 +16；服务器查询键 +68 | GetCharacterInfo/GetBasicCharacterInfo 的原生构造路径 |
| 服务器缓存 | 0x5DCB9D8 / 0x5DCB9E0 | 查询路径 0x254F1D0；缓存项有效位与名称位置由对应指令确认 |

IDA 伪代码有栈/局部变量警告；上述关键偏移另对照 Go x86asm 解码的运行时指令。分析只更新离线 IDA 副本。小范围原生函数代码与 JSON 报告保存在本机忽略目录 `data/manual-session/charselect-memory/`，不包含完整进程 dump。

## 原生读取器单独使用时的边界

- 原生列表的顺序与可见行、战团分组、折叠、搜索、方向键顺序还未验证。
- 列表正在重新加载、断线弹窗、登录错误还缺独立状态判定；两次相同的列表不证明不存在弹窗。
- 离开选角页只能说明该标志为 false，不能独立区分加载中、游戏内、登录页；游戏内仍接已有插件会话标记。
- 已测试两次完整重启，第二次覆盖模块基址变化；尚未测试换版本，当前只适配固定 SHA。模块基址每次从所选进程重新取得。
- 状态结果始终返回 `readyForAutomation: false`；尚未完成无人值守流程。

2026-10-30：增加独立显式入口 `-collect-config`，结合这里的原生读取、插件新心跳与小退文件校验执行采集，不依赖上面那个单独就绪字段。实现和实测结果见 [后台采集说明](../../BACKGROUND_COLLECTION.md)。已实测通过一次逐角色归档及进入第二角色；任意错误弹窗/版本兼容性仍未全面验收。此前单次 Up 复测已按用户要求取消，未补发。

## 单次后台方向键实验

在默认读取参数外显式加 `-key Down` 或 `-key Up`，并提供 `-expect-guid '<刚读到的完整 GUID>'`。`-character` 与 `-realm` 必须匹配发送前的当前选中角色。报告路径必须不存在。

发送前重新读取状态并核对角色、GUID、选角标志；游戏必须在后台且有可见窗口，Shift/Ctrl/Alt/Win 均不得按住。单次发送扩展方向键的 WM_KEYDOWN/WM_KEYUP，记录前台 HWND，等待后重新读取原生状态。消息入队不等于游戏执行，结果需要对照 `.before.json` 与 `.after.json`。失败不自动重试、不抢焦点，不发送回车、聊天或宏。

`-native` / `-functions` 是原生入口/代码采集研究模式；`-discover-strings` 是初期字符串候选搜索，不参与正常状态读取，也不作为任何就绪判断。

## 参考资料的边界

- [BabelChat](https://github.com/Yumash/BabelChat)：仅参考已完成的插件主动发布标记通道，不用于证明选角页原生结构。
- [旧版 CGlueMgr 研究](https://github.com/samwhosung/wow-1121-client-internals/blob/main/docs/glue.md)：1.12.1 的架构线索，地址不能套用当前正式服。
- [客户端选角 UI 源码镜像](https://github.com/Gethe/wow-ui-source/blob/live/Interface/AddOns/Blizzard_GlueXML/Mainline/CharacterSelect.lua)：解释 UI 语义，不证明当前进程的内存地址。
- GUID 位布局参考本地固定 TrinityCore ObjectGuid.h；服务器名称最终来自当前客户端原生缓存，不来自离线名称表。
