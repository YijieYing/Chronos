# Chronos 功能调查 · 2026-09-05

## 结论与范围

Chronos 已有可复用的后端主链和本地日程原型：可以创建、修改、删除时间轴对象，持久化提案、
澄清与执行记录，并采集和展示粗粒度活动状态。距离“个人日程和状态管理助手”的主要缺口是
理解准确性、操作反馈、真正的提醒投递、实际执行记录、状态参与规划，以及可靠的后台生命周期。
后端架构主干建立不等于这些产品闭环完成，也不等于所有兼容代码已删除。

本轮只调查和更新文档，不修改产品代码、不重启服务、不调用真实模型、不改用户日程。
调查依据是当前磁盘上的工作区，不是对正在运行的 Mac WebView 的验收。

- Git 基线：`95fc39b`（`main`，最近一次已有代码提交）。工作区还有此前未提交的功能改动，
  包括提醒编辑、中文时间/时长、多 Event 澄清、灵活安排、演示启动与场景测试。
- 本次 `PYTHONPATH=src python3 -m unittest discover -s tests`：**193 项通过**。
  其中含未跟踪的 `tests/test_demo_scenarios.py`；许多测试使用静态模型、确定性 Interpreter
  或 legacy 组件，不能据此声称真实 LLM 或前端端到端已验收。
- 本次 `npm --prefix web run build`：TypeScript/Vite 构建通过。未做浏览器点击或原生通知验收；
  `web/package.json` 没有前端行为测试命令。
- 本次提交仅包含文档。因此只拉取此次 GitHub 提交，并不能得到全部下述工作区功能与 193 项测试。

状态定义：**完成（限定范围）**指该项边界内实现和已有验证齐全；**部分**指组件存在但用户闭环缺失；
**未实现**指只有规划/契约或尚无生产路径；**缺陷**区分本轮纯函数复现、静态代码确认和历史报告待实测。

## 现在有哪些功能

| 功能 | 状态 | 已有能力及边界 | 代码 / 验证入口 |
| --- | --- | --- | --- |
| canonical Agent 主链 | 完成（基本路径） | Items → Events → Plan → Operations → Runtime；持久化 Snapshot、Plan 和生命周期；部分旧辅助依赖仍在 | `agent/flow.py`、`planner.py`、`lowerer.py`、`runtime.py`；`test_agent_boundaries.py` |
| Parser 原文锚定 | 完成（保守分段） | 默认整条 Prompt 为一个 Item；支持注入精确 span 分段器，尚无生产复杂分句器 | `agent/parser.py`；`test_agent_parser.py` |
| Task 手动创建/编辑/删除 | 完成（基本 CRUD） | 标题、日期、时长、强度、fixed、重复规则；编辑整条 series。缺少开始/完成/实际时长闭环 | `TaskComposer.tsx`、`timelineStore.ts`、v1 Schedule 路由 |
| Task 自然语言 CRUD | 部分 | 基本创建、选中目标编辑标题/时间/时长、删除可执行；目标不存在有结构化 conflict。多字段保真、复杂引用仍有缺陷 | `test_agent_flow.py`、`test_agent_pipeline.py` |
| Reminder 手动 CRUD | 完成（基本 CRUD，工作区） | 共用 TaskComposer，双击打开面板、保存/删除；点或窗口提醒；手动更新保留 status/source/created_at。Undo 和点击细节未闭环 | `ReminderBeacon.tsx`、`TaskComposer.tsx`、`reminders/service.py`、`test_schedule_v1.py` |
| Reminder 自然语言 CRUD | 部分 | 创建、改标题/触发时间、删除；不占 Schedule 时长；最终 Reminder Event 丢弃 duration Gap。仍依赖 kind 判定正确；Agent 更新会重建提醒状态 | `agent/interpreter.py`、`planner.py`、`runtime.py` |
| 重复任务 | 部分 | daily/weekly、指定周几、inclusive until；一条 series 存储，后端投影有限时域内的 occurrence。简单“持续一周”有测试；复杂截止、取消重复、单次例外未完整支持 | `schedule/planner.py`、`service.py`、`test_agent_pipeline.py` |
| 日内约束排程 | 完成（基础能力） | Schedule 按优先级/期限/固定块排程，支持可拆分任务、未排入余量、Agenda 版本。不能等同于 Agent 已支持任意自然语言拆分和全局优化 | `test_schedule_planner.py`、`test_schedule_service.py` |
| 灵活安排与相对移动 | 部分 | “找时间”可找空档；有固定约束保护；相对移动目前是硬编码 20 分钟和向后搜空位，未解释完整偏移量和偏好 | `agent/planner.py:_edit`、`test_demo_scenarios.py` |
| 两层 Interpreter | 部分 | 一次 LLM 提取后字段归一化；中文时长/钟点、标题清洗有补丁；仍无完备字段证据覆盖，规则可覆盖正确模型值 | `agent/interpreter.py`，见 B01–B04 |
| 多任务 Prompt | 部分 | 一个 Item 可生成多个 Event；占用区间可逐项累积；澄清支持 Event 锚点及缺失 Event 合并。共享原文导致钟点串用，重排 Event 仍有风险 | `_normalize_event`、`_merge_previous_events`、`test_agent_interpreter.py` |
| clarification | 部分 | Gap 阻断 Plan/Operations；版本化 Answer；Log Peek/抽屉可回答和切换。缺少完整字段稳定性、取消/重试体验；错误策略和产品约定不一致 | `flow.py:clarify`、`ChronosLog.tsx`，见 B04 |
| 提案确认/拒绝/日志/投影 | 完成（基本路径） | 多个 canonical 待处理项；Apply/Reject；独立 Projection；legacy 历史只读并排除活跃待确认状态 | `projection_service.py`、`log_service.py`、v1 路由及相关测试 |
| stale 处理 | 部分 | 范围相交时标记失效并隐藏投影；当前要求重新提交，旧文档的自动重编译已不成立 | `api/routes/v1.py:_timeline_changed` |
| Autonomy L0–L3 | 部分 | 持久化策略、风险/歧义/影响门槛及可逆操作直接执行；UI 自动执行后不完整刷新；不代表已有自主重排能力 | `agent/autonomy.py`、`test_autonomy.py`、`timelineStore.ts` |
| Runtime 执行/回滚/Undo | 部分 | 有 before/after、事务记录、失败补偿及 Agent Undo；跨仓库不是单一数据库事务，无完整崩溃恢复/后续编辑冲突保护 | `agent/runtime.py`、`test_agent_runtime.py` |
| 时间轴和 Overview | 部分 | 平移、缩放、框选、对象选择、属性面板、周视图拖动/缩放；提醒命中、双击创建竞争、Overview 只定位不选择仍需统一 | `WaveTimeline.tsx`、`OverviewMap.tsx`，见 B08 |
| macOS 原生采集 | 完成（当前采集器） | 输入聚合、前台应用/窗口、会话信号；权限降级；不记录键盘内容。生产多设备合并、后台常驻不在此完成范围 | `apps/mac-agent`、`monitor/live.py`、`test_live_loop.py` |
| 当前状态/近期状态历史 | 完成（规则估计） | CognitiveState 五分钟桶持久化，load/fatigue/focus 分开；live/history/demo 标识；属于启发式估计，未做个人校准效果验证 | `monitor/cognitive.py`、`service.py`、`useLiveMonitor.ts` |
| 实际任务归属/状态纠正 | 未实现（产品闭环） | 有活动类别及内存活动段，不等于知道正在做哪个 Task；无完整用户反馈、完成和实际耗时记录界面 | `monitor/models.py`、`agent/state.py`、Task 表单 |
| Forecast / 预计完成时间 | 部分 | 前端 MonitorAdapter 可算预计延迟和六小时曲线；后端没有权威 Forecast、误差反馈和持久化任务归属 | `web/src/monitor/MonitorAdapter.ts`；`agent/state.py` 只有 now/timezone |
| 被动调整信号 | 完成（只检测记录） | missed_task/fixed_conflict/cognitive_overload 可去重记录；不投提案、不改日程。missed 不代表已观测到用户没做 | `agent/adjustment.py`、`test_adjustment_engine.py` |
| 提醒真正到时投递 | 未实现 | 有时间、投递意图和状态 API；没有到时 evaluator、系统通知、投递回执/重试/稍后提醒 | `reminders/`、`apps/mac-app`；不可把 Beacon 亮起视为已通知 |
| 智能择机提醒 | 部分（仅意图） | 可表达 context-aware/avoid_high_focus 等意图；部分策略只到 Plan/Operation，未进入 Reminder 持久化；无实际执行器 | `ReminderDraft`、`ReminderSpec`、Runtime reminder create |
| Profile / MEMORY SYNC | 部分 | Markdown/ZIP 导入、去重、候选审核、接受/编辑/遗忘和本地检索已有；canonical Flow 未接入 profile/检索上下文 | `schedule/agent_memory.py`、`agent_profile.py`、`MemorySync.tsx`；B05 |
| 查询/解释对话 | 部分 | Directive 可显示 Log 回复，当前只读对象索引缺少完整日程/状态；无可靠查询执行层，多 Directive 也未完整路由 | `Flow._objects/_build`、`Interpreter._prompt` |
| Residue 能力缺口收集 | 部分 | Registry、SQLite repository 和单测存在；生产启动/Flow 未接 capture，缺少真实失败采集和复盘入口 | `agent/residue.py`、`sqlite_residue.py`、`test_residue_registry.py` |
| 本地启动及演示 | 部分 | Schedule 脚本、Mac shell 和 demo 工具存在；进程唯一性、代码新鲜度、演示端口一致性未解决 | `scripts/run-mac-app.sh`、未跟踪 demo 脚本；B09/B12 |

以上源码相对 `src/chronos/`，前端文件相对 `web/src/`，测试相对 `tests/`。
“完成”项也需随 P0 补充跨层验收；不将表格中的限定组件能力扩张成完整产品承诺。

## 缺陷与实现到一半的关键边界

### B01 · P0 · 多 Event 的时间、日期相互污染（本轮复现）

`Parser` 默认一条 Item；`_normalize_event` 为每个 Event 都拼出整个 Item 的 `source_text`。
`_normalized_time` 又优先从整条原文取第一个钟点，覆盖模型提供的时间。这使“明天早上九点读书，
下午三点跑步”即使模型输出两个正确 point，最终也变成两个 09:00。明确日期如“9月10日早上九点”
也被只识别今天/明天的规则改成当前日期 09:00。

“九点变七点”的单场景修复存在，但不能因此认定时间理解已完成。需要 Event/字段级证据范围，
日期、钟点、时区联合校验和冲突处理；不能只提高整句正则的优先级。

### B02 · P0 · recurrence 边界丢失（本轮复现）

`_normalized_recurrence` 总是优先使用原文规则结果；识别到“每天”就可能以 `until=None` 覆盖
模型正确解析出的“这周结束”。“每天九点持续一周”若今日九点已过，time 会滚到明日，until
仍从今天计数；日期写法、周边界和首次实际执行日没有共同依据。Agent 取消重复也没有明确表示：
Planner 的 recurrence edit 要求非空 recurrence。

### B03 · P0 · 正确模型字段仍可能被丢弃或覆盖（静态确认）

标题清洗大量依赖“提醒/安排/小时”等词语；未区分标题本身含这些词的情况。
`_merge_previous_events` 用标题、Item 覆盖数和基于 index 的 Event id 做合并，尚不是稳定的字段修订；
carry 分支按回答字段名删除 Gap，即使回答未成功解析。真实 LLM 原始响应未保存，无法从最终 Snapshot
反推“当时模型给了 0、none 还是其他形状”。本轮不把归一化错误归因成模型的原始回答错误。

### B04 · P0 · 降级/报错策略与已确认产品原则不一致（静态确认）

当前 `schedule_server.main` 无条件给有 Model 的 Flow 注入 `fallback_interpreter=Interpreter()`；
`Flow._interpret_snapshot` 捕获 primary exception 后自动调用它。无效 JSON/空 meanings 现在抛异常，
无 fallback 的测试会进入 failed；生产组装则可能走规则解释并继续生成可执行结果。
因此“配置 semantic”不证明每次结果来自 LLM，也不符合此前“不能静默降级”的约定。

后续验收必须分清：语义不清楚 → anchored clarification；格式不合格 → 有限修复后保留输入并进入
可恢复的 clarification，不能声称已经理解或自动写入；
provider 不可用 → 技术状态可见且不写日程。语义能否继续需由可靠证据决定，不能自动猜值。
产品如希望所有情况都出统一卡片，可统一 UI，但不能让用户用回答时长来修网络故障。

### B05 · P1/P3 · 记忆与 State 没进入决策（静态确认）

启动创建了带 memory retriever 的旧 command parser，但 `Flow(Interpreter(model), ...)` 不接收它。
canonical `_prompt` 仅有 Items/选择/对象索引/时间/previous，无已接受个人记忆和 Profile。
Planner `State` 仅有 `now` 与 `timezone`。故“已导入记忆”“正在显示负荷”都不代表它们影响日程。

### B06 · P0 · 自动执行后的 UI 同步与手动 Undo 不完整（静态确认）

`runAgent` / `answerOperation` 刷新 Log 和 Projection，但不会像手动 Apply 一样重载 tasks/reminders；
L1+ 自动接受可能出现后端已创建、前端不显示。`answerOperation` 对 failed 返回也未主动抛出错误。
提醒手动编辑/删除记录含 `manual_action`，`ChronosLog.canRestore` 因此显示 Undo，但 `restoreLog`
没有对应 `update_reminder` / `delete_reminder` 分支，点击会返回。需用真实交互验收确认表现。

### B07 · P0 · 编辑保真、事务恢复边界（静态确认）

`TaskDraft` 不携带完整 task_type/intensity/spectrum；Lowerer 全量更新创建默认 `TaskSpec`，
Runtime 会把 task_type 写回，存在改标题同时改变任务分类的路径。
Agent `UpdateReminderOperation`/move 通过 delete + create 实现，create 默认 pending，旧 delivered/done
可能丢失；手动 Reminder update 已使用 replace 保留状态，两条路径并不等价。
`Runtime.revert` 没有比较 after-state 与当前对象，就恢复 before-state，可能覆盖后续修改；
跨域写入和日志是多次数据库操作，异常补偿测试通过不代表断电/进程退出原子性。

### B08 · P0 · 点击、双击和对象定位（静态确认；设备实测待做）

提醒按钮已扩至 56px 并加双击编辑，但背景邻域判定只检查 x 距离 32px，没有 y 距离，
整条竖向空白都可能被提醒截走。背景创建仍使用 180ms timer，较慢双击之前可能已打开创建面板；
邻域选择与按钮双击也不是同一命中路径。提醒单击 toggle，Task 单击仅 select，仍不完全一致。
Overview 点击提醒只 focusTime，不写提醒 selection。需一套二维命中和完整 pointer/click/dblclick 验收。

### B09 · P0/P2 · 启动、重复采集和发热（历史已报告，当前代码仍有风险）

launcher 只用 `adjustment-signals-v1` 判断 backend 是否可复用，不校验代码版本；Monitor live 状态
不是启动锁，退出只终止直接启动的 PID。此前多次运行出现重复采集器和 WebKit 高占用。
当前仍有逐对象无限动画和背景模糊。本轮没有重新测 CPU/GPU，不沿用旧采样数字作为当前测量。

### B10 · P0/P4 · 相对移动、冲突与 stale 仍是基础版（静态确认）

`Planner._edit` 对 later/earlier 使用固定 20 分钟；用户的“推迟一小时”未被完整作为偏移量建模。
多个修改分支不能保证维持既有 window；大多数规划错误仍是 PlanningError，只有 missing target
明确转换为结构化 conflict。失效处理是 stale + 重新提交，尚无可审查的增量重新规划。

### B11 · P1–P4 · 产品声明超出实现（静态确认）

Reminder 没有真实投递；具体中断偏好没有完整领域持久化；Registry 无生产接线；多 Directive
只取首个回复、Event 和 Directive 混合时 Planner 拒绝处理；查询对象索引也不是完整 Schedule 查询。
这些应列为缺口，而非“功能已支持，只差界面”。

### B12 · P0 · 演示工具和质量基线（静态确认）

未跟踪 `demo-start.sh` 使用 8765，而 `demo_full_chain_check.py` 的 BASE 是 8766；
后者会调用 reset，因此本轮没有执行它。demo 测试的重复成功常用确定性 Interpreter/静态 Model，
不能替代真实 provider 的多次语义评测。生产时钟与 demo-now 也需要隔离验收。

## 本轮可复核的无写入实验

固定 `now=2026-09-05 08:00 Asia/Tokyo`，注入静态 Model 给 `Interpreter`，不调用网络或 Runtime：

| 原文 / 模型正确字段 | 实际归一化结果 | 判定 |
| --- | --- | --- |
| 明天早上九点读书，下午三点跑步；模型给次日 09:00 / 15:00 | 两个次日 09:00 | B01 确认 |
| 9月10日早上九点读书；模型给 09-10 09:00 | 09-05 09:00 | B01 确认 |
| 这周每天早上九点读书；模型 recurrence.until=09-06 | daily / until=None | B02 确认 |
| 明天早上九点读书；模型只给 morning | 次日 09:00 | 单钟点补丁有效，但覆盖面有限 |

复现入口：`Parser().parse(...)` → `Interpreter(StaticModel(...)).interpret(..., now=..., timezone=...)`。
前三行已经说明即便 LLM 正确，第二层也能产生错误。本轮不新增产品测试文件；将这些输入纳入下一轮 P0 回归。

## 原计划写过但尚未完整交付

| 原规划 | 当前缺口 | 下一阶段 |
| --- | --- | --- |
| 稳定 Interpreter、全字段证据、Residue 复盘 | 多 Event/日期污染、静默降级、Registry 未接入、无真实语义评测 | P0、P1 |
| 完成所有 legacy 清理 | 写入口已切换，但 compiler/semantic_parser/agent_interpretation 仍留存；Runtime 还 import proposals 辅助转换 | P0 随功能收尾 |
| deadline/偏好/复杂关系/历史补录/广泛重排 | Schedule 局部字段和 IR 有契约；canonical 用户闭环不足 | P1、P4、P5 |
| Reminder 通知、择机投递、状态控件、密集聚合、Task ↔ Reminder | CRUD 有了，真实 delivery 和转换/聚合缺失 | P1、P2、P4 |
| 窗口外后台常驻、进程身份、启动锁、缓冲/幂等重放、登录启动 | launcher 管理子进程；无完整后台服务 | P2 |
| 后端 Forecast / observed task / overrun / drift | 前端预测、类别估计、被动信号；不形成主动重排闭环 | P3、P4 |
| 个人上下文接入和更多来源 | 手动导入和审核可用，canonical 检索未接；无实时同步、Calendar/Notes/Octopus/MCP 提交口 | P3、P5 |
| 记忆冲突、过期、来源权限、加密/恢复 | 部分冲突候选/版本记录已有，完整治理未完成 | P3、P5 |
| 视觉活动理解 | 仍为设计；无截图/OCR/脱敏/视觉推理生产链 | P5 可选 |
| 状态桶迟到修订、多设备、长期汇总、个人校准 | 五分钟估计已有；详细设计的多设备选择、两桶修订、小时/日汇总和效果评估未交付 | P3、P5 |
| Updater 自动发现能力缺口并生成审查补丁 | 无生产缺口收集闭环，更无 Updater | P5 后置 |

后续功能顺序和验收定义见 [roadmap.md](roadmap.md)。旧路线和阶段记载保存在
[迁移路线归档](archive/roadmap-2026-08.md) 与 [历史 Agent 审计](agent-interaction-audit.md)。
