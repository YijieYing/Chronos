# Chronos

Chronos 是本地优先的个人日程和状态管理助手原型。它把自然语言请求转成可审阅的日程操作，
展示任务、提醒和活动状态；长期目标是把计划、实际执行、个人状态与可控的主动建议连接起来。

## 当前进度

截至 2026-09-05，后端主链已建立，产品正在做可靠性收尾，尚不能作为已完成的自动日程助手。

- 已有：Task/Reminder 基本 CRUD、日内排程及 daily/weekly 重复、澄清与提案、日志和投影、
  自治策略基础、macOS 活动采集、规则认知状态与近期历史、个人资料导入管理。
- 尚未闭环：多事项时间理解、澄清稳定性、编辑/Undo 保真、前端执行反馈与提醒命中、
  后台生命周期。到时系统通知、实际执行管理、状态参与规划仍待实现。
- MEMORY SYNC 能导入和管理资料，但当前 canonical Flow 尚未使用该检索上下文。
  前端 Forecast 是估计展示，不是后端权威预测；无新鲜观测时的 DEMO DATA 不代表真实状态。

完整清单和可复现缺陷见[功能调查](docs/feature-audit-2026-09-05.md)，
开发顺序见[产品路线图](docs/roadmap.md)，职责见[架构](docs/architecture.md)。
调查覆盖工作区中此前未提交的代码；本次文档提交不包含那些功能改动。

## 架构

```text
Prompt → Parser → Items → Interpreter → Events → Planner → Plan
       → Lowerer → Operations → Runtime → Schedule / Reminder
```

Proposal、Log、Projection 围绕这条主链提供生命周期与展示，不拥有另一份可执行真相。
Monitor 独立采集与估计状态；当前还没有完整接入 Agent 的规划上下文。

```text
src/chronos/
├── agent/           # 解释、规划、操作、授权、日志和投影
├── schedule/        # 任务、约束、Agenda 与有限时域重复投影
├── reminders/       # 不占排程容量的提醒
├── monitor/         # 活动证据与状态估计
├── infrastructure/  # SQLite 等适配器
└── api/             # 本地 HTTP 与 CLI
apps/mac-agent/      # 原生采集器
apps/mac-app/        # WKWebView 桌面壳
web/                # React / TypeScript 时间轴
```

## 启动

Python 核心要求 Python 3.12+，无第三方运行时依赖。前端需要 Node/npm；macOS 原生组件需要 Swift 工具链。

首次安装和构建前端：

```bash
npm install --prefix web
npm --prefix web run build
```

浏览器使用：

```bash
./scripts/run-schedule.sh
```

打开 [本地 Chronos](http://127.0.0.1:8765)。默认 SQLite 数据库为
`data/chronos.sqlite3`，服务默认监听 localhost，静态前端来自 `web/dist/`。
修改前端后需重新构建；修改后端或模型配置后需重启对应服务。

macOS 桌面使用：

```bash
./scripts/run-mac-app.sh
```

启动器会构建/打包前端、尝试复用或启动本地服务，并在没有活跃采集时启动 Monitor。
当前复用主要依赖 capability，不保证已有服务是最新代码；出现“修了但没变化”时先核对运行实例，
不要反复启动叠加进程。窗口退出后的完整进程清理与后台常驻尚待验收。直接运行 Swift 壳不会管理 Python 服务。

开发热更新：在两个终端分别运行 API 与 Vite。

```bash
./scripts/run-schedule.sh
```

```bash
npm --prefix web run dev
```

前端开发配置见 `web/vite.config.ts`。可用 `CHRONOS_WEB_URL` 指向本地开发 URL。
仅测试原生采集流可运行 `./scripts/run-mac-loop.sh --device-id my-macbook`；
不要和已经运行的桌面采集器重复启动。

## 模型与个人资料

参考 `config/agent.example.toml` 配置 Git 忽略的 `config/agent.local.toml`；
可通过 `CHRONOS_AGENT_CONFIG` 或 `--agent-config` 覆盖路径。不要提交密钥。
无可用模型配置时可运行确定性解释路径，但它不等价于 LLM 的自然语言能力。
当前生产模型异常还有自动 fallback 路径，这是 P0 待修缺陷，不是推荐的产品行为。

MEMORY SYNC 支持 Markdown 和 ChatGPT/Claude 导出 ZIP，提供候选审核、接受、编辑和遗忘。
Markdown 模板见 `config/personal-profile-import.example.md`。
导入原文件保存在 Git 忽略的 `data/agent-imports/`，可用 `--agent-import-dir` 调整。
目前资料存储/检索组件与 canonical Flow 的使用链路未接通；不要据导入成功推断本轮 LLM 已使用资料。
`config/agent.local.md` 及旧 profile 注入机制也不能当作新主链已接通的证据。

## 采集权限与数据

macOS 原生采集器汇总输入数量、前台应用/获授权的窗口标题和会话状态，不记录按键内容或完整指针轨迹。
Input Monitoring 用于全局输入计数，Accessibility 用于窗口标题；权限不足时各采集器独立降级。
在系统设置的“隐私与安全性”授权后重启采集器。本版本不使用屏幕录制。

状态估计在本地按规则运行，五分钟桶保存在 SQLite；它不是健康诊断或经验证的生产力测量。
配置、数据库、导入文件和可能包含私人 Prompt 的诊断输出都应留在本地。

## 验证

```bash
PYTHONPATH=src python3 -m unittest discover -s tests
npm --prefix web run build
git diff --check
```

本轮工作区：193 项 Python 测试和前端构建通过；含未提交的场景测试，多数语义测试使用静态模型/
确定性解释器。未调用真实 provider，未验收浏览器点击或原生通知。详细范围见功能调查。

## 文档索引

- [当前功能、完成度与缺陷](docs/feature-audit-2026-09-05.md)
- [P0–P5 产品路线图](docs/roadmap.md)
- [当前架构与扩展边界](docs/architecture.md)
- [认知状态专项设计与实现边界](docs/cognitive-state-estimator.md)
- [视觉活动理解：未实施设计](docs/visual-activity-understanding.md)
- [旧路线图归档](docs/archive/roadmap-2026-08.md)
- [Agent 迁移历史](docs/agent-interaction-audit.md)
