# HugoFgo 架构与接口

版本：v1。范围：**Vue 前端与 Tauri/Rust 壳复用**，Python 执行器独立成包，作业存储与资源任务归属桌面壳。术语见 [领域术语](../GLOSSARY.md)。

本页同时描述当前模块边界和已实现的本地接口；尚未落地的目标会单独标注。这里没有 HTTP 后端、REST/GraphQL 路由、API 端口或应用层鉴权。

## 1. 进程模型

一个桌面进程（Tauri）+ 按需一个 Python 执行器子进程（ADR-0001）。执行器自带设备层；壳的 adb 代码只服务设备预览，避免高频战斗路径跨进程。

```text
┌────────────────────────── Tauri 桌面进程 ──────────────────────────┐
│  Vue 前端                                                          │
│      │ Tauri commands + 事件订阅                                    │
│  壳 Shell：作业存储 / 资源任务 / 设备查询 / 运行控制                 │
└───────┬───────────────────────────────────────────────┬───────────┘
        │ 启动参数 + stdin 停止 + stdout JSON Lines + 退出码│ adb（设备查询与预览）
        ▼                                                 ▼
┌──────────────── 执行器子进程 ─────────────────────┐   模拟器
│  作业模型 → 执行器核心 → 设备网关                  │      ▲
│                ↑            ↑                      │      │
│           画面感知      运行会话（日志/证据）        │      │
└────────────────────────────────────────────────────┘      │
                            │ adb / scrcpy（截图与输入）─────┘
```

壳与执行器之间的进程缝包括启动参数、stdin 控制、stdout JSON Lines 和退出码。任何一方可独立替换，但必须遵守第 3 节契约。

## 2. 模块与接口

设计原则：每个模块一个小接口，复杂实现藏在后面；调用方与测试走同一个缝。不引入“未来可能有用”的缝。

### 2.1 作业模型 Job Model

- **接口**：`compile_job(文本) → 执行计划 | 诊断`。
- **实现**：Schema 校验、槽位解析、面/回合展开、能力支持检查、错误定位到面与回合。
- **机器契约**：`schemas/job-v1.schema.json`；人读说明见 [作业格式](job-format.md)。
- **当前与目标**：执行器从 Schema 校验；前端目前仍维护手写 TypeScript 类型。“前端也从 Schema 生成”是目标，不是当前实现。

### 2.2 设备网关 Device Gateway

- **接口**：`devices()` 与 `Device` 协议的 `metrics()`、`frame()`、`tap(x, y)`、`swipe(x1, y1, x2, y2)`。
- **坐标**：动作使用 1280×720 基准，由适配器换算到真实分辨率；serial 与区服属于 `DeviceConfig`。
- **适配器**：adb（实机截图与输入）、fake（回放喂截图、记录动作）、ldplayer-window（可选快速截图，仅 Windows + LDPlayer）、scrcpy（显式启用的持续帧与输入）。
- **LDPlayer 边界**：原始帧适配器只截图，`tap`/`swipe` 会明确报错；当前完整实机闭环仍走 adb，不把该通道算作独立刷本通道。
- **scrcpy 边界**：持续解码线程只保留最新帧，每次感知读取要求新帧并校验新鲜度、尺寸与会话身份；输入使用同一设备的控制 socket。触摸消息按固定 3.3.3 协议使用类型 2、完整 32 字节字段和同一 finger ID；40 ms 点击期间发送三次静止 MOVE，滑动约每 16 ms 采样移动。启动失败仅在用户预先确认后可见回退 adb，运行中断直接安全停止。固定服务端由 `resources/scrcpy/` 分发并校验，通道不进入作业 Schema。
- **分辨率**：仅支持精确 16:9 横屏且不低于 1280×720；其他分辨率拒绝执行（ADR-0005）。
- **区服**：设备配置必填并决定识别配置与图库；当前执行器只接受 CN（ADR-0003）。

### 2.3 画面感知 Perception

- **接口**：`read(帧) → 观察`；`read_fields(帧, 字段, timeout)` 在同一帧上同步补充字段。`timeout` 只为调用方兼容保留，不会排队或等待。执行器只消费观察，不碰像素。
- **身份**：助战候选通过头像区域与概念礼装图库匹配输出身份，不使用文字 OCR。普通再临与灵衣头像都归一到原从者 ID，队伍核对使用同一身份语义。普通指令卡使用 Atlas `commands` 原图，优先取上方 120 像素头像区做 SIFT 匹配，避开大面积卡色特效文字；失败时回退原完整查询区，内点阈值和身份差距不放宽。SIFT 仍不足时使用 ORB 独立特征匹配兜底，不通过降低阈值或牌组构成猜测身份。随后独立输出卡色和助战标记；助战卡脸被特效遮挡、SIFT 不足时，若助战标签仍有足够分数且编队只有一个助战槽位，可按该槽位确认身份，队伍页核对仍须先通过。`bind_formation(编队)` 用于同名从者槽位消歧，`bind_mystic(御主礼装)` 绑定队伍页需核对的御主礼装。队伍页分别输出三位先发从者、三位后排从者、按实际助战槽位读取的概念礼装和御主礼装字段，不固定读取第一列；核对作业指定槽位不符时不得开始任务，空槽位不核对实际身份。战斗就绪页通过前排立绘输出 `battle_servants`，值为原始编队槽位；未识别、同身份消歧失败或过程帧淡化时返回 `uncertain`，不得把未知身份当成空槽补位。
- **资源加载**：绑定编队后只加载当前作业所需身份素材；未绑定的离线分析使用内置少量素材，不对整套图库做特征提取。感知不读取作业文件。
- **图像字段**：面、回合与指令卡暴击率用 HSV 白字掩码和连通域分割数字；暴击率额外过滤星形与百分号，并同时容纳两位/三位数。NP 用灰度阈值处理暗帧，在宝具条局部区域内做数字模板滑窗、连通域约束和非极大值抑制。所有结果写回该帧缓存，不拼接其他帧。助战、队伍、控件和弹窗仍只使用模板、颜色与布局证据。
- **未执行字段**：AP 与敌人数量不参与实机执行；请求时返回 `uncertain`，不保留文字 OCR 或自动猜值后门。
- **观察内容**：基页、版式变体、覆盖层、控件、字段状态、帧哈希与证据。从者技能控件另含 `on_cooldown` 三态冷却测量，以图标下方「剩余 N」标签为判据；执行器确认上一技能进入冷却后才推进。`unknown / loading` 不输出可行动控件。
- **字段状态**：`not_applicable / not_read / uncertain / confirmed`，区分“不需要读”和“读取失败”。
- **耗时与缓存**：观察证据记录本次数字识别的 `field_ms` 与 `field_cache_hit`；`Perception.stats()` 汇总普通读帧和字段读取的耗时、读取次数与缓存命中，并随 `RunResult` 写入运行目录的 `result.json`。
- **中间态**：攻击/技能/换人动画归为 `battle + animation`；结算分 `bond / exp / loot`。被模态覆盖层或目标选择变体盖住时，不输出基页控件。单帧出现两个及以上宝具区域时，执行器要求连续两帧一致证据，避免技能特效被当成额外宝具卡。自身目标技能遇到尚未反映动作的旧可操作帧时不得提前加速，以免越过随后出现的目标面板；若加速后才出现目标面板，则安全停止，不再执行乱序目标选择或第二次加速。技能冷却与普通手牌的多帧确认规则统一由执行器核心执行，见下节。
- **当前限制**：只有国服识别配置；面/回合/暴击率已有指定人工帧的数字模板断言，但模板只覆盖已采集字形，完整留出集和修复后实机验收未完成。

### 2.4 执行器核心 Executor

- **接口**：`run(执行计划, 设备网关, 画面感知, 事件出口, options, control) → 运行结果`。
- **实现**：画面状态机、动作后确认、补刀策略、安全停止、连续出击计数、AP 白名单与清 AP（ADR-0004）。每场维护原始槽位对应的当前前排、替补队列和优先级接替锚点；主动换人确认后交换前排与替补，自动补位要求连续两个战斗就绪帧一致，跨面保持并在下一场重置。全局与按面策略分别按各自原始顺序计算接替后的指定从者顺序，空槽实际从者保持低层；显式宝具卡位和普通卡身份不因排序改写。助战列表只有在重复行且最后一行下缘达到 `650` 时才刷新，其他位置继续滚动，持续无变化则停止。真实最后一页下缘为 675，覆盖该阈值；不能把 `720` 当成唯一底部证据，避免未知候选被误判为列表末尾。礼装图像匹配与后续满破标识识别属于不同证据，满破状态要求暂不作为当前作业字段。
- **测试缝**：假设备 + 截图语料 + 假感知 = 回放，不另开测试专用行为入口。

#### 战斗动作与选卡确认

- **技能生效门槛**：相邻战斗动作至少间隔 1 秒，动作后仍需画面恢复确认。从者技能控件通过图标下方「剩余 N」标签输出三态 `on_cooldown`：`true` 表示冷却中、`false` 表示仍可用、`None` 表示该帧没有测量。冷却中的技能不点击，持续 30 秒未转好以 `skill_unavailable` 停止；点击后必须确认该图标进入冷却，才允许下一技能、宝具或普通卡继续。实测仍可用时，2 秒后开始观察，连续约 0.5 秒确认仍可用才最多补点一次当前技能，补点同样以图标仍可用为前提；目标面板已出现时不再补点。该位置已换成其他从者时豁免原技能冷却要求；没有冷却测量的帧保持原确认规则。御主技能和令咒不套用从者技能冷却判据。
- **普通手牌首帧路径**：五张普通卡均存在、卡色可读、没有未知从者且能按当前作业选出三卡时，首张手牌仍可立即提交，不为多帧防护增加固定等待。宝具区域继续使用独立的稳定确认和位置映射，不参与普通卡投票。
- **多帧读取路径**：出现未知身份、身份不足或前后结果冲突时，中间帧只作为样本，不直接提交。执行器按卡位保存最近四次从者槽位和卡色，分别取多数结果；已知身份至少两次结果形成多数，任一历史出现未知身份时至少读取三次。平票或没有多数结果时继续等待，持续无法确认则在动作超时后安全停止。比较的是卡位、从者和颜色，不要求像素位置或整帧内容一致，因此允许卡片轻微来回移动。
- **空槽与未知身份**：`-1` 始终表示未知，不直接等于空槽实际从者。即使编队存在空槽，单帧未知也不能作为任意从者卡提交；只有多帧结果仍无法归属指定从者时才保留任意从者语义。后续多数证据若指向指定从者，必须恢复指定身份。指定从者与空槽任意从者的两层优先顺序不因投票改变。
- **颜色与锁卡**：投票在排序之前完成。五张卡同属希耶尔时，从者优先级各项相同，卡色仍按作业倾向比较；单帧把一张红卡误认成蓝卡，不得直接形成最终三卡。正常首张完整且无异常的手牌不经过额外投票，避免把防护变成每回合固定延迟。
- **提交与证据**：三卡初始间隔为 350 ms，整链后仍以新画面确认。提交事件同时记录全部五张普通卡的从者和颜色、最终卡序、批次、恢复次数、实测间隔与攻击到首卡耗时，并在运行目录保存提交帧和对应观察 JSON；后续审计可区分识别错误、排序错误与点击未生效。选卡页停留 2 秒仍按新帧最多恢复一次，部分漏卡只补选实际剩余后缀。
- **攻击动画超时**：攻击结束后，如果当前仍是战斗画面，确认等待上限为 90 秒，覆盖宝具与敌方行动的连续动画；选卡未提交、技能无响应、选卡页残留和其他页面继续使用原 30 秒保护，不把较长的动画等待扩大成全局超时。

### 2.5 运行会话 Run Session

- **接口**：事件出口 + `RunControl.stop()`；产出运行目录与历史记录。`write_run_summary(运行目录)` 生成中文动作摘要，`python -m executor.session.summary <运行目录> [--job <作业>]` 为既有运行重新生成。
- **实现**：动作日志、失败截图、状态快照、结果分类、历史索引、作业/执行计划快照与中文摘要。摘要按场、面、回合和动作整理，使用作业名称与相对耗时，分别标注已点击、画面确认与没有独立生效确认；原始 `events.jsonl` 不改写，证据不足时不推断结果。
- **停止语义**：收到停止后在当前动作边界结束。壳最多等待终端事件 30 秒，超时才强制终止进程树并记录异常停止。

### 2.6 壳 Shell

- **接口**：设备查询、作业 CRUD、资源任务、运行控制、事件订阅、运行历史与证据路径定位。
- **实现**：执行器进程生命周期、文件读写、设置持久化、前端事件转发。
- **不包含**：战斗逻辑或识别逻辑。

### 2.7 前端 UI

- 按准备、作业、运行、记录四个工作区组织：准备管理设备、区服、资源、默认通道与回退；作业管理编队、操作序列、选卡策略、刷本次数、AP 白名单和作业库；运行显示启动控制与事件时间线；记录读取真实历史与证据。
- 编队页与启动流程共用同一份持久化运行设置；AP 恢复按用户勾选顺序解释，勾选圣晶石时弹出显式消耗确认。
- 作业编辑按 Schema 保留完整数据；启动前执行编译与运行能力检查，诊断定位到面与回合。能保存不等于能运行。
- 运行面板消费真实 `run-event`，区分已发送动作、画面确认、警告、通道变化与终端结果；历史页读取壳返回的真实记录和证据摘要。
- 运行状态在全局可见；浏览器预览没有桌面命令、真实运行、资源任务或历史能力。

## 3. 当前接口契约

这些是本地产品和开发集成入口，不是跨版本稳定 SDK。普通使用优先走桌面 UI；本地自动化优先使用 Job JSON、`python -m executor` 和 JSON Lines 事件。

### 3.1 Python 执行器 CLI

```bash
python -m executor <job.json> \
  --serial <adb serial> \
  --region CN \
  [--run-dir <目录>] \
  [--battles <场数>] \
  [--recovery [saint_quartz|gold|silver|bronze|copper ...]] \
  [--drain-after-turns] \
  [--adb <adb路径>] \
  [--channel adb|ldplayer-window|scrcpy] \
  [--allow-adb-fallback] [--scrcpy-server <固定服务端路径>] \
  [--fake]
```

- `--region` 必填，当前只接受 `CN`。`--channel` 默认 `scrcpy`，ADB 作为启动失败保底。
- 省略 `--recovery` 时默认仅允许 `bronze`；单独写 `--recovery` 且不带值表示空白名单。
- `--drain-after-turns` 在固定场数完成后清 AP，此阶段禁用恢复道具。
- `--fake --serial fake` 不接触设备，仅用于联调。
- 开发期默认写入项目 `logs/farm/<时间戳>/`；安装版由壳传入应用数据目录。

其他常用入口：

```bash
python -m executor.domain <job.json> [--json]          # 校验与编译
python -m executor.perception <截图> [--ready] [--json] # 单帧感知
python -m executor.replay [--only <名称> ...]           # 执行器回放
python -m executor.session.summary <运行目录> [--job <作业.json>] # 重新生成中文动作摘要
python -m executor.device --self-test                  # 设备自检
```

### 3.2 stdin、stdout 与退出码

壳向执行器 stdin 写入单独一行 `stop\n`，请求动作边界停止。

stdout 与 `events.jsonl` 每行一个 JSON 对象：

```json
{
  "type": "action",
  "timestamp": 1760000000.0,
  "data": {
    "control": "攻击",
    "wave": 1,
    "turn": 1,
    "action_index": 0,
    "x": 1050,
    "y": 620,
    "reason": "battle_action",
    "wait_ms": 0.0,
    "channel_ms": 1.2,
    "input_finished_at": 1760000000.01
  }
}
```

事件类型包括：

| 类型 | 含义 |
| --- | --- |
| `run_started` | 进程与作业启动信息 |
| `state_changed` | 感知基页变化 |
| `wave_started` / `turn_started` | 面或面内回合推进 |
| `action` | 实际设备动作及坐标、原因、面/回合；战斗动作另含 `wait_ms`、`channel_ms` 与输入完成时间；批量选卡另含批次、卡位、从者槽位、卡色、牌库索引、实测间隔与攻击到首卡耗时 |
| `card_batch_submitted` | 一次三卡提交的批次、恢复次数、卡序、配置间隔及全部五张普通卡的从者与颜色；须结合后续确认或恢复事件判断成败 |
| `action_confirmed` | 动作后画面确认；`stage` 标识等待阶段，`wait_ms` 记录该阶段耗时 |
| `warning` | 可恢复等待、字段降级等非终端警告；有限动作恢复记录控件与次数 |
| `recovery_used` | 后续画面确认后的恢复消耗 |
| `battle_completed` | 单场结算完成及累计场数；前端在 `finished` 前逐场更新，只用于进度观测 |
| `drain_started` | 固定场数完成，进入清 AP |
| `finished` / `stopped` / `failed` | 完成、停止、失败终端事件 |

退出码：**0 完成；1 停止或运行失败；2 非法作业**。终端原因以事件和 `result.json` 为准，不能只凭退出码判断具体结果。

### 3.3 Tauri commands

前端通过 `invoke<T>(command, args)` 调用。Rust 的 `AppHandle` 和 `State` 由 Tauri 注入；命令成功返回 `T`，失败 reject 为字符串错误。

| 范围 | 命令 | 前端参数与用途 |
| --- | --- | --- |
| 设备 | `list_devices` | 无；返回 adb 设备数组 |
| 设备 | `get_device_info` | `{ serial }`；返回分辨率、DPI、型号 |
| 设备 | `capture_frame` | `{ serial }`；返回 PNG base64、临时路径和尺寸 |
| 设备 | `tap_normalized` | `{ serial, x, y, width?, height? }`；归一化坐标测试点击 |
| 运行 | `start_run` | `{ serial, jobPath, settings, region }`；启动唯一执行器进程 |
| 运行 | `stop_run` | 无；请求动作边界停止 |
| 运行 | `get_run_state` | 无；返回当前或最近一次运行快照 |
| 作业 | `list_jobs` / `read_job` | 列表；`read_job` 接收 `{ file }` |
| 作业 | `save_job` / `delete_job` | 分别接收 `{ name, job }`、`{ file }` |
| 历史 | `list_run_history` | `{ limit? }`；默认 100、最多 500 |
| 历史 | `get_run_record` / `open_run_artifacts` | 均接收 `{ id }`；后者返回证据路径、`result.json`、最近事件和画面文件 |
| 历史 | `open_run_directory` | `{ id }`；在系统文件管理器中打开证据目录 |
| 运行 | `validate_job` | `{ jobPath }`；调用领域编译器返回摘要与带面/回合的诊断 |
| 资源 | `check_resources` / `verify_resources` | `{ region }` |
| 资源 | `update_resources` | `{ region, refresh }`；`refresh=true` 表示完整校准 |
| 设置 | `get_run_settings` / `save_run_settings` | 读取；保存接收 `{ settings }` |

`RunState` 的 `notice` 显示可见回退等提示，`channel` 记录当前实际设备通道；`channel_changed` 会同时更新两者。

`start_run` 示例：

```ts
const state = await invoke('start_run', {
  serial: 'emulator-5554',
  jobPath: 'jobs/example.json',
  region: 'CN',
  settings: {
    battles: 2,
    recovery: { allowed: ['bronze'], drain_after_turns: false },
    device_channel: 'adb',
    allow_adb_fallback: false,
  },
});
```

恢复项为 `saint_quartz | gold | silver | bronze | copper`；圣晶石只有在用户白名单内且确认弹窗道具一致时才允许确认。新设置的默认通道是 `scrcpy`，启动失败可回退 `adb`；已保存的显式通道值保持不变。`RunState` 使用 snake_case：`running/run_id/serial/job_name/job_path/started_at_ms/status/screen/wave/turn/action/completed_runs/recovery_used/log_dir/error/notice/channel/exit_code`。

前端还使用：

- `run-event`：载荷为 `{ run_id, type, timestamp, data }`。
- `resource-task-output`：载荷为 `{ region, action, stream, text }`。
- `hugofgo-data` 自定义协议：只读本地资源；Windows 上表现为 `http://hugofgo-data.localhost/...`，不监听网络端口。

### 3.4 作业、运行与历史文件

作业输入以 `schemas/job-v1.schema.json` 为准。运行设置：

```json
{
  "battles": 1,
  "recovery": { "allowed": ["bronze"], "drain_after_turns": false },
  "device_channel": "scrcpy",
  "allow_adb_fallback": true
}
```

`device_channel` 的新配置默认为 `scrcpy`；已保存的显式 `adb` 选择保持不变。`allow_adb_fallback` 仅控制启动失败后的可见回退，运行中中断不会跨通道续跑。

每次运行目录可包含：

| 文件 | 内容 |
| --- | --- |
| `events.jsonl` | 完整事件流；生成摘要不会改写 |
| `summary.md` | 按场、面、回合与动作组织的中文摘要，区分输入、画面确认与没有独立生效确认 |
| `job.json` | CLI 启动时保存的原始作业快照（成功读取作业后） |
| `plan.json` | 核心执行器写证据时保存的执行计划快照，供独立重新生成摘要 |
| `result.json` | 状态、原因、进度、作业、设备、起止时间、最后观察与失败帧路径 |
| `observation.json` | 最后一帧感知结果 |
| `final.png` / `final.bgr24` | 完成或停止时的最后一帧（可用时） |
| `failure.png` / `failure.bgr24` | 非完成状态下的最后一帧（可用时） |
| `fake-actions.json` | 仅假运行的模拟动作记录 |

`logs/run-history.jsonl` 每次终端事件追加一条 camelCase 记录：`id/jobName/jobFile/serial/region/channel/startedAt/finishedAt/durationMs/result/reason/battlesCompleted/recoveryUsed/runDir/error`。`channel` 是终端时实际通道，可见回退后记录为 `adb`。

### 3.5 Python 内部模块入口

这些属于内部开发 API，没有独立版本化或兼容承诺：

- `executor.domain.compile_job(text)`：作业校验与编译。
- `executor.core.run(plan, device, perception, events, options=..., control=...)`：执行核心。
- `executor.device.open_device(config)`：打开设备适配器。
- `executor.perception.Perception.read(frame)` / `read_fields(...)`：单帧感知。
- `executor.session.JsonlEventSink` / `MemoryEventSink` / `RunControl`：事件与停止控制。
- `executor.session.summary.write_run_summary(directory, job_path=None, plan=None)`：生成或重新生成中文动作摘要。

## 4. 存储布局

| 位置 | 内容 | 说明 |
| --- | --- | --- |
| `jobs/` | 作业 JSON | 格式 v1；安装版位于应用数据目录 |
| `data/<区服>/` | 图库元数据、素材、资源清单 | 按区服隔离，图库不随安装包分发 |
| `logs/run-history.jsonl` | 每次运行一条历史索引 | 开发期在项目目录，安装版在应用数据目录 |
| `logs/farm/<运行ID>/` | 每次运行的日志与失败截图 | 本地证据，不入库 |
| `captures/` | 验证素材与索引 | 回放和模板源，不是实机运行输入 |
| `schemas/` | 作业 JSON Schema | 执行器机器契约 |

## 5. 目录结构

```text
src/            前端
src-tauri/      壳
executor/       Python 执行器
  domain/       作业模型与编译
  device/       设备网关（adb + fake + 可选 ldplayer-window / scrcpy）
  perception/   画面感知
  core/         执行器状态机
  session/      运行会话与历史
schemas/        作业 JSON Schema
captures/       验证素材
```

`cv/` 只保留离线分类、模板构建与统一回放等验证工具；实机执行统一由 `executor/` 承担。

## 6. 测试策略

- **主缝**：执行器接口上的回放（截图进、动作与停止出）。
- **契约层**：Schema 校验测试覆盖非法作业。
- **实机故障**：每个故障补一条回放用例，永久保留。
- **证据边界**：离线、真实截图、实机和安装版验收分别记录，见 [测试与验收](testing.md)。

## 7. 关键取舍

- **单执行器**：设备访问、识别、执行各一份实现。
- **进程隔离**：执行器崩溃不拖垮桌面端；代价是维护事件协议。
- **契约前置**：作业 Schema 先行，减少格式理解漂移。
- **识别与执行分离**：感知可独立演进，不动状态机。
- **区服维度正交**：区服只影响设备配置、识别配置与图库路径，不进入作业格式与状态机。
