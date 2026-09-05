<h1 align="center">
  <code>iterate-harness</code>
</h1>

<p align="center">
  <a href="README.md"><strong>English</strong></a> ·
  <a href="README.zh-CN.md"><strong>简体中文</strong></a>
</p>

<p align="center">
  <a href="https://www.npmjs.com/package/iterate-harness"><img src="https://img.shields.io/npm/dt/iterate-harness?label=Downloads&style=for-the-badge&color=2ea44f&logo=npm&logoColor=white" alt="npm downloads"></a>
</p>

<p align="center">
  <a href="#-快速开始"><img src="https://img.shields.io/badge/快速开始-5_分钟-blue?logo=github&logoColor=white" alt="Quick Start"></a>
  <a href="#-iterate-特性"><img src="https://img.shields.io/badge/Iterate-7_工具-ff69b4" alt="Iterate Tools"></a>
  <a href="#-iterate-特性"><img src="https://img.shields.io/badge/模式-dry--run_|_normal-61DAFB" alt="Modes"></a>
  <a href="#-双模式架构"><img src="https://img.shields.io/badge/任务模式-code_%7C_iterate-7c3aed" alt="Task Modes"></a>
  <img src="https://img.shields.io/badge/python-%E2%89%A53.10-blue?logo=python&logoColor=white" alt="Python">
  <img src="https://img.shields.io/badge/dynamic/json?url=https%3A%2F%2Fregistry.npmjs.org%2Fiterate-harness%2Flatest&query=version&label=version&color=brightgreen" alt="Version">
  <a href="LICENSE"><img src="https://img.shields.io/badge/License-MIT-yellow" alt="License"></a>
  <a href="https://github.com/jingzhao-l/iterate-harness"><img src="https://img.shields.io/github/stars/jingzhao-l/iterate-harness?style=social&label=Star" alt="Stars"></a>
</p>

**iterate** 是一个让 AI 编程助手具备多轮自主代码审查与修复能力的开源项目。它解决很具体的痛点：

> AI 助手往往"说得多、做得浅"：一次对话只改几行、看过一个文件就不再管全局，也很少回头复核自己改坏的东西。iterate 把这些收尾工作——逐项审查、分维度排查、修复、验证、再迭代——自动化，让 AI 真正像资深工程师一样把改动做完、做对。

在该生态内，**iterate-harness** 是面向 iterate 评审/修复闭环的专用 agent harness：
多维度代码评审反复执行直到发现**收敛**，确定性聚合，每轮验证的原子修复，
以及全程可审计的 append-only 决策日志。它是**三个可互换组件**之一，三者共用同一套
`iterate.config.yaml` 与维度体系：

| 组件 | 形态 | 面向场景 |
| --- | --- | --- |
| [**Core Skill + CLI**](https://github.com/jingzhao-l/iterate-skill) | 可移植 AI 技能 `/iterate` + `iterate` CLI | 在 Trae / Claude Code / Cursor / Copilot / Codex 等 25+ 助手的对话式界面里迭代 |
| **iterate-harness** | 独立无头引擎（`ih`，npm: `iterate-harness`） | **本仓库** —— 在终端 / CI / Git 钩子里运行同一闭环，无需对话式助手 |
| [**iterate-plugin**](https://github.com/jingzhao-l/iterate-plugin) | dsh 桌面客户端插件 | 把 iterate 的收敛仪表盘 / review 进度带进 dsh 界面 |

它是一款**独立的专属 agent harness**，围绕 iterate review/fix 闭环打造：
内核 agent loop、React TUI、工具/技能/插件体系与权限层全部为 iterate 原生，
核心是移植自 iterate skill TypeScript 实现的语义层，以及引擎级的收敛控制策略。
**v2.0 引入双模式架构**：`task_mode` 在 `iterate`（1.x 审查/修复闭环）与
`code`（通用编程 agent 模式，叠加防御式内核保证）之间切换——见
[#双模式架构](#-双模式架构)。

> ⭐ 如果这个项目对你有帮助，欢迎点亮 GitHub Star，这是对开源维护最大的支持！

---

## 🐳 OrcaRouter（内置网关——含免费模型）

[OrcaRouter](https://www.orcarouter.ai/ref/ref_5eca75a9c809c95ab152) 是内置的
OpenAI 兼容网关供应商，**内置免费模型**——如 `deepseek/deepseek-v4-flash-free`
或 `orcarouter/free` 路由——**$0 计费、无 token 成本**，只需一个 API Key。

**还没有 Key？** 在 Key 提示处直接按回车，终端会唤起浏览器打开注册页
（通过上方链接注册，即支持本项目）：

```bash
ih setup orcarouter     # 粘贴已有 Key，或按回车打开注册页
```

已有 Key？激活内置 profile——**只需一个环境变量**（`ORCA_KEY`）；base URL
（`https://api.orcarouter.ai/v1`）与默认模型（`orcarouter/auto`）都已内置：

```bash
ih provider use orcarouter
export ORCA_KEY=sk-orca-...
```

> 免费档说明：免费模型仍需 API Key；长上下文提示可能超出免费档的 prompt
> 上限（HTTP 429 且无 `Retry-After`），最适合 CI / 轻量评审。

---

## 🚀 快速开始

```bash
# 安装（npm 包装器；需要 Node + Python >= 3.10）
npm install -g iterate-harness
# ……或无 Node 一键安装（macOS / Linux / WSL，仅需 Python）
# curl -fsSL https://raw.githubusercontent.com/jingzhao-l/iterate-harness/main/scripts/install.sh | bash

# 启动 TUI
ih

# REPL 内
/iterate review        # dry-run：只读多轮评审，直到收敛
/iterate personalize   # 方向键 9 类个性化向导（选择弹窗 + 输入弹窗，无需切终端）
```

也可以走 CLI：

```bash
ih iterate onboard       # 模型驱动项目扫描 -> ITERATE.md 知识库 + 配置 + manifest 指纹
ih iterate onboard --no-ai # 纯检测降级路径（不调模型，channel=cli）
ih iterate status        # 配置 + onboarding 状态 + 漂移检查
ih iterate refresh       # 重新采集指纹、报告漂移、刷新元信息（不调模型）
ih iterate reonboard     # 备份后完整重扫，用户手写区逐字保留
ih iterate personalize   # 9 类个性化向导：约束写入 config + ITERATE.md 用户区
ih iterate init          # 检测项目，生成 iterate.config.yaml（仅配置）
ih iterate review        # 无头 dry-run（支持 stream-json 输出）
ih iterate review --changed # 快审：只审相对 --ref（默认 HEAD）改动的文件
ih iterate review --template strict # 评审模板：standard（默认）/ strict / quick
ih iterate run           # 无头自治修复闭环
ih iterate run --template quick  # 以不同评审模板跑修复闭环
ih iterate validate "pytest -x"  # 运行预配置校验命令；输出 allowed / reject_reason / exit_code
ih iterate sessions      # 列出已存会话（--limit N / --json），再 `ih iterate resume --session <id>` 续接
ih iterate resume        # 恢复上次会话
ih iterate log           # 查看决策日志尾部
ih iterate log --trend   # 查看跨运行 finding 趋势（新增/已修复/回归/顽固）
ih iterate log --replay  # 按时间序回放整次运行（相对时间戳）
ih iterate report        # 渲染最终报告（CI 模式，见下）
ih iterate report --pr    # 把报告发布/更新为 PR 评论（gh CLI，幂等）
ih iterate report --html # 单文件 HTML 报告（收敛曲线、内嵌 diff、可直接分享）
ih iterate batch a/ b/   # 顺序评审多个仓库，按严重度加权排出最差榜
ih iterate schedule add "0 9 * * 1-5" # 每日 changed-only 快审（cron，UTC）
ih iterate cron start|stop|status|history  # 管理执行定时任务的后台 cron 调度守护进程
ih iterate hook install  # 托管 pre-commit 钩子：1 轮 changed-only 提交门禁
ih iterate doctor        # skill↔harness 维度体系一致性检查
```

Onboarding 说明：`ih iterate onboard` 先做模型凭证门禁（`ih auth login`），
然后让模型用自己的只读工具扫描项目（manifest、2-3 层目录树、specs/tests/CI、
README——绝不碰 `.env`/密钥），按字节精确的「AI 维护区 / 用户维护区」标记写出
`ITERATE.md`；harness 代码负责校验标记、采集 manifest SHA-256 指纹并写
`iterate.config.yaml`——不可信的模型输出永远进不了受信配置结构。两个产物与
skill 侧 onboarding 字节兼容（同标记、同 `onboarding.fingerprints` schema），
两个生态互相可读。之后每次循环 kickoff 都会把 `ITERATE.md` 知识库注入系统提示，
manifest 漂移（升依赖、换技术栈）会在评审前给出非阻塞警告。

先设置 API Key：`export ANTHROPIC_API_KEY=your_key`（也支持
OpenAI 兼容供应商，见 `ih --help`）。

## 🧭 双模式架构

自 v2.0 起，`task_mode` 决定 **agent 做什么**（与决定 **能碰什么** 的
`permission_mode` 正交）：

| 模式 | 行为 |
| --- | --- |
| **`iterate`**（默认） | 经典 1.x 闭环——确定性多维度评审、逐维度分诊、每轮验证的原子修复、append-only 决策日志 |
| **`code`** | 通用编程 agent 模式：全套工具照常可用，叠加**防御式内核**，让每一次编辑天然安全 |

CLI 用 `--task-mode` 切换，TUI 内对空输入按 **Tab** 在 `code` ↔ `iterate`
之间循环（输入框左侧竖向模式色条与底部模式文字随模式变色——code=primary、
iterate=amber）。code 模式 leader 派生的子代理继承同一防御式内核，子代理写
代码时享有同等保证（CLI → AppState → `agent_tool` → subprocess backend，
设计 §20.5）。

### 防御式内核（`code` 模式）

三项机械保证（设计 §20.3.2）——由代码强制，而非靠提示词：

1. **原子事务**——每个变更类文件工具执行前快照；失败自动回滚（fail-fast +
   原子事务）。
2. **不变量守护**——每次编辑成功后重跑项目不变量，违规回滚并作为工具错误
   上抛，模型必须回应。不变量来自 `iterate.config.yaml` 的 `invariants` 段
   （`ensure` 文件断言 + `commands` 逐模块命令表），无 `invariants` 段时回退
   `validation.commands`。命令仅在精确匹配时执行，且拒绝含 shell 串联
   元字符的命令。
3. **假设审计**——agent 经 `record_assumption` 声明的假设写入决策日志，
   假设被证伪即为 fail-fast 信号。

```yaml
# iterate.config.yaml —— 为 code 模式声明项目不变量
invariants:
  ensure:
    - pyproject.toml
    - src/main.py
  commands:
    syntax:
      - python -m py_compile src/main.py
    tests:
      - pytest -x
```

## ✨ Iterate 特性

| 能力 | 说明 |
| --- | --- |
| **确定性评审引擎** | `iterate_review` plan / aggregate / meta-review：跨轮去重、`known_intentional` 过滤、severity 排序、收敛统计、6 项报告一致性审计——全部纯计算，零 LLM 判断 |
| **两种模式** | `dry-run`（纯评审，绝不改文件）与 `normal`（评审 → 原子修复 → 验证 → 循环，验证失败经 git 隔离自动回滚） |
| **引擎级收敛强制** | `IterateLoopPolicy` 位于内核查询循环：轮次上限、收敛自停、下一轮引导不受 prompt 注入影响 |
| **收敛仪表盘** | React TUI 实时面板：逐轮 findings 趋势、带分维度 USD 估算的维度分布、主循环累计成本、收敛徽标 |
| **findings 分诊** | `iterate_triage`：逐条 `y` 修复 / `n` 跳过 / `a` 永久忽略；`a` 持久化到 `known_intentional`，后续轮次自动过滤 |
| **成本透明** | token 用量按内置价格表换算为每轮/累计 USD（可按模型覆盖） |
| **安全边界代码化** | 设置中的 `protected_paths` 与 `forbidden_fix_patterns` 自动装配进权限层（deny 路径规则 + 写载荷正则）；验证命令走精确匹配白名单 |
| **逐修复 diff 审批** | `require_fix_approval` 让 normal 模式闭环中的每次文件写入都走带 diff 预览的交互确认——即便处于全自动模式；硬拒绝（保护路径/禁止模式）绝不降级为可确认 |
| **Esc 中途干预** | 闭环运行中按 Esc：在下一轮边界暂停并弹出方向键菜单（跳过当前 finding / 收窄维度 / 直接停 / 继续）；再按一次 Esc 强制打断当前 turn |
| **finding 指纹趋势库** | 每次收尾用 `文件\|行号\|维度` 指纹把 finding 记入 `.iterate/trend-library.json`；`ih iterate log --trend` / `/iterate trend` 跨运行统计新增 / 已修复 / 回归 / 顽固（连续 3+ 轮）finding |
| **断点续跑** | TUI 启动画面汇总上一次收尾（结论、轮数、严重度分布、最后一次干预），`/iterate resume` 基于决策日志续跑并复核仍然复现的 finding |
| **CI / PR 模式** | `ih iterate report --github --fail-on high` 将最终报告转为 GitHub Actions 批注，并按严重度门禁决定退出码；`--pr` 经 gh CLI 把报告以 Markdown 评论发布（marker 查找全量翻页，巨型 PR 同样幂等更新同一条评论），所有失败形态优雅降级、绝不破坏退出码策略 |
| **changed-only 快审** | `--changed [--ref <ref>]`（CLI 与 `/iterate review --changed`）把整个闭环钉在 git 增量上：kickoff、评审计划与每个维度 reviewer prompt 都携带明确的改动文件清单 |
| **批量排行** | `ih iterate batch repoA repoB …` 顺序评审多个仓库，按严重度加权排出最差榜；单个仓库失败不会中断整批 |
| **定时评审** | `ih iterate schedule add "0 9 * * 1-5"` 注册 cron 任务，每日（UTC）自动跑 changed-only 快审（`--clean-ok`）；新增 vs 顽固 finding 经趋势库呈现 |
| **cron 调度守护进程** | `ih iterate cron start|stop|status|history` 管理执行定时任务的后台守护进程——启动一次后，定时评审无人值守持续运行，可查历史（`--limit` / `--json`） |
| **CLI 校验执行器** | `ih iterate validate "<command>"` 在 shell 中以一次性方式运行预配置校验命令，输出 `allowed` / `reject_reason` / `exit_code`——适合在 CI 里编排防御式 pre/post-check，与工具层共用同一执行器 |
| **会话列表** | `ih iterate sessions` 列出已存会话（summary / model / 时间戳 / 消息数，`--limit` / `--json`），再用 `ih iterate resume --session <id>` 精确续接——不必记住上次跑的是哪次 |
| **评审模板** | `review` / `run` 支持 `--template`：在 `standard`（默认）、`strict`（保守、安全优先）与 `quick`（只看影响面）三种评审提示词模板间切换 |
| **HTML 单文件报告** | `ih iterate report --html` 把整次运行渲染成一个可离线打开的 `.html`：SVG 收敛曲线、severity/维度分布条、含失败场景的 findings 表、按修复着色的 diff——可直接作为 CI 产物分享 |
| **评审回放** | `ih iterate log --replay` 按时间序回放整次运行（`[+90s] r1 review_result newFindings=3`），像看录像一样还原闭环展开过程 |
| **per-dimension 资源** | `iterate.config.yaml` 的 `dimension_resources` 支持按维度设置 `model` / `concurrency`（1–8）/ `token_budget`——security 用强模型、style-tests 用快模型；评审计划会携带到每次 reviewer 派发 |
| **token 预算强制** | `token_budget` 在引擎层封顶整轮运行（超限硬停并转入收尾报告）；`iterate_review(operation="aggregate", dimension_usage=…, dimension_usage_io=…)` 审计各维度用量，把 reviewer 上报的累计 token 回传引擎成本表——上报 input/output 拆分的维度按精确单价计费、仅上报总量的按混合价估算——下一轮自动跳过已超限维度 |
| **阈值门禁** | `thresholds.max_critical` / `max_high` / `max_medium` / `max_low`（全局或按维度）封顶最终报告中的发现数量——违规即把结论翻转为 `needs_revision`，并让 `ih iterate report` 退出码失败（`threshold gate: FAIL`） |
| **定时评审时区** | `ih iterate schedule add "0 9 * * 1-5" --timezone Asia/Shanghai` 按本地时区解释 cron（存储为 UTC 标准化时间），"每天 9 点"就是你所在地的 9 点 |
| **检测式 init** | `ih iterate init` 探测项目标记文件（package.json / pyproject / go.mod / Cargo.toml / …），基于真实证据推断测试命令，推荐评审维度（前端依赖解锁 `frontend-backend` / `ui-ux`），预览 yaml 并确认后才写入；TUI 内 `/iterate init` 同效 |
| **模型驱动 onboarding** | `ih iterate onboard` 串联凭证门禁 → 检测证据 → 模型扫描 → `ITERATE.md` 知识库（AI/用户分区标记）+ manifest 指纹；`refresh` 重采指纹、`reonboard` 保留手写区重扫；每次 kickoff 注入知识库并检测漂移——产物与 skill 生态字节兼容。TUI onboard 的指纹会在下一次 review/run 自动补录，无需手动 `refresh` |
| **个性化向导** | `ih iterate personalize` 走完 skill 同款 9 类（禁区、风险区、已知意图、维度定制、修复优先级、禁止修复方式、注意点、代码约定、补充验证命令）：结构化规则写入 `iterate.config.yaml`（禁区同时由内核权限层强制拦截），自由文本写入 `ITERATE.md` 用户区，每次 kickoff 附带约束；补充命令先过严格白名单再合并进 `validation.commands`。TUI 内 `/iterate personalize` 以方向键菜单流跑同一向导（分类菜单带实时条目数 → 增删 → 保存/继续编辑/放弃）；无头会话保留摘要 + CLI 指引 |
| **pre-commit 钩子** | `ih iterate hook install` 写入带标记的托管 `.git/hooks/pre-commit`：提交前跑 1 轮 changed-only 评审并按 `--fail-on` 严重度门禁；拒绝覆盖第三方钩子，可用 `ITERATE_SKIP_HOOK=1` / `--no-verify` 跳过 |
| **维度 doctor** | `ih iterate doctor` 一条命令检查整个维度体系：内置 canonical 定义 vs harness 内部常量 vs 你的 `iterate.config.yaml`（未知维度键、惰性的资源/门禁条目、超出启用集的个性化引用）；发现漂移退出码 1，可直接做 CI 门禁 |
| **决策日志** | append-only `.iterate/decision-log.jsonl`：每轮、每次修复、验证与分诊决策全部落盘 |
| **项目知识** | `ITERATE.md` 项目知识 + 按项目隔离的 9 类结构化个性化数据 |
| **双模式架构** | `task_mode`（`code` / `iterate`）与 `permission_mode` 正交：`iterate` 保留 1.x 审查/修复闭环，`code` 为通用编程 agent 模式、叠加防御式内核——CLI 用 `--task-mode`，TUI 按 Tab |
| **防御式内核（code 模式）** | 原子事务（快照 → 失败自动回滚）、不变量守护（`invariants.ensure` + `invariants.commands`，无段时回退 `validation.commands`；命令精确匹配、拒绝 shell 元字符）与假设审计（`record_assumption` → 决策日志）——全部由代码机械强制 |
| **worker 防御内核继承** | `--task-mode` 经 CLI → AppState → `agent_tool` → subprocess backend 全链路穿透（设计 §20.5）：code 模式子代理以同一防御式内核运行 |

## 🔧 七个 iterate 工具

- `iterate_config` — 生效配置（默认值 + `iterate.config.yaml` 覆盖）
- `iterate_validate` — 运行预配置验证命令（仅精确匹配）
- `iterate_review` — 确定性引擎：plan / aggregate / meta-review
- `iterate_decision_log` — append-only 决策日志
- `record_assumption` — 声明 / 验证一条假设，写入决策日志（code 模式审计轨迹）
- `iterate_context` — SKILL.md / ITERATE.md / 个性化上下文
- `iterate_triage` — 交互式 y/n/a findings 分诊，`a` 持久化 known_intentional

`/iterate` 斜杠命令（status / review / run / log / config / validate）与
内置 `iterate` skill 提供同样闭环的不同入口。

## 🧭 架构

```
src/iterate_harness/
├── iterate/            # 语义层（TS skill 的 Python 移植）
│   ├── review.py       # 去重 / known_intentional 过滤 / severity 排序 / 收敛
│   ├── meta_review.py  # 6 项报告一致性审计
│   ├── config_loader.py# Master + Overrides 合并
│   ├── validate.py     # 精确匹配验证执行器
│   ├── decision_log.py # append-only JSONL
│   ├── loop_policy.py  # 引擎级收敛强制 + 成本计量
│   ├── personalization.py # 9 类按项目存储
│   ├── worktree_flow.py# git 隔离：enter/commit/exit + 回滚
│   └── prompts.py      # canonical dry-run/normal 循环模板
├── defensive/          # code 模式防御式内核（设计 §20.3.2）
│   ├── kernel.py       # 每查询协调器：快照 → 后检 → commit/rollback
│   ├── transaction.py  # 原子文件事务缓冲
│   ├── invariants.py   # ensure 断言 + 精确匹配命令守护
│   └── assumptions.py  # 假设审计轨迹 → 决策日志
├── engine/             # 内核 agent loop（上游 + iterate 控制块）
├── permissions/        # 权限检查 + iterate 自动装配（protected_paths…）
├── tools/iterate_tools.py  # 七个 iterate_* 工具
└── ui/                 # React TUI 后端宿主 + review_progress 协议
```

## 📦 安装

- **npm（最简）**：`npm install -g iterate-harness` —— 轻量包装器，首次运行时把
  发布 tarball pip 安装进托管 venv（`~/.iterate-harness-npm`），版本与 npm 包同步
- **macOS / Linux / WSL**：`bash scripts/install.sh`（克隆 + venv + 可编辑
  安装，把 `ih` 与 `iterate-harness` 链入 `~/.local/bin`）
- **Windows (PowerShell)**：`scripts/install.ps1`
- **本地检出**：`bash scripts/install_dev.sh`
- 依赖 Python ≥ 3.10；Node.js ≥ 18 启用 React TUI（缺失时自动跳过，
  纯文本回退 UI 仍可用）

## 🧪 测试

```bash
python -m pytest tests/test_iterate -q   # 语义层 + 内核集成
python -m pytest -q                      # 全量
```

## 📄 许可与致谢

MIT。iterate-harness 维护于
[jingzhao-l/iterate-harness](https://github.com/jingzhao-l/iterate-harness)。
iterate 语义层源自 [iterate-skill](https://github.com/jingzhao-l/iterate-skill)
项目。

## ⚠️ 免责声明

本项目按「现状」（AS IS）提供，不附带任何明示或暗示的担保，包括但不限于对适销性、特定用途适用性及不侵权性的担保。

**自动化的代码审查与修复存在固有风险。** normal 模式下产生的改动均由 AI 模型生成，可能引入缺陷、回归或非预期行为。在合并改动前，你应当：

- 在应用到主分支或推送前，逐条 review 每一处 diff。
- 确保项目处于 git 版本控制之下，并可随时回滚（`git restore`、revert 或从备份恢复）。
- 在每轮修复后运行项目自身的测试与构建检查。
- 切勿在密钥、凭证、`.env` 或任何不允许修改的文件上运行本项目；请在配置中设置 `protected_paths` 予以保护。

使用者需为本项目使用过程中所产生、修改或提交的代码负全部责任。使用本项目即表示你同意：维护者与贡献者不对因使用本项目而导致的任何损失、损害或法律后果承担责任。

## 故障排查 / 常见失败自愈指南

### TLS / SSL 证书错误
**症状**：调用模型 API 时出现 `SSL: CERTIFICATE_VERIFY_FAILED` 或 `certificate verify failed`。

**成因与修复**：
1. **系统 CA 证书过旧**——运行 `pip install --upgrade certifi`，或更新操作系统证书。
2. **企业代理 / 中间人**——把 `REQUESTS_CA_BUNDLE` 或 `SSL_CERT_FILE` 环境变量设为你企业的 CA 证书。
3. **本地自签名端点**——若使用本地模型服务（ollama、lmstudio），在 provider profile 里设置 `auth_source: local`（对 localhost 关闭证书校验）。

### 认证 / API Key 错误
**症状**：调用模型 API 时出现 `401 Unauthorized` 或 `403 Forbidden`。

**成因与修复**：
1. **Key 缺失或过期**——运行 `ih provider use <profile>` 按交互提示重新输入 Key。
2. **认证源错误**——确认 provider profile 的 `auth_source` 与你的凭证槽位一致。用 `ih provider list` 检查，再用 `ih provider edit <name>` 更正。
3. **被限流**——见下方「限流 / 配额」章节。

### 限流 / 配额超限
**症状**：`429 Too Many Requests` 或配额耗尽错误。

**成因与修复**：
1. **每分钟请求过多**——在 harness 设置或 `iterate.config.yaml` 里设置 `max_turns_per_minute` 来节流。
2. **token 预算超限**——在 `iterate.config.yaml` 里设置 `token_budget` 或 `budget_usd` 封顶单轮开销。
3. **供应商账户配额**——查看供应商用量面板，必要时升级套餐。

### 断点 / 续跑失败
**症状**：`Resume` 找不到上次检查点，或检查点已过期。

**成因与修复**：
1. **检查点被清除**——一次成功运行会清除检查点；只有未完成/被打断的运行才持有有效检查点。
2. **残留 worktree**——若开了 `worktree_isolation: true`，此前异常退出可能残留过期 worktree，运行 `git worktree prune` 清理。
3. **人工改动**——如果你在 worktree 里手动改过文件，检查点可能失效，请重新开始一次运行。

### 供应商 / 模型不存在
**症状**：`model not found` 或 `unknown provider` 错误。

**成因与修复**：
1. **模型名拼写错误**——运行 `ih provider list` 查看可用供应商及其默认模型。
2. **自定义供应商配置错误**——运行 `ih provider edit <name>` 核对 `base_url`、`api_format`、`default_model` 字段。
3. **本地端点未启动**——对本地 / ollama 供应商，确认服务在运行：`curl http://localhost:11434/api/tags`。
