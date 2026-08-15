<h1 align="center">
  <img src="assets/logo.png" alt="iterate-harness" width="64" style="vertical-align: middle;">
  <br>
  <code>iterate-harness</code>
</h1>

<p align="center">
  <a href="README.md"><strong>English</strong></a> ·
  <a href="README.zh-CN.md"><strong>简体中文</strong></a>
</p>

**iterate-harness** 是面向 iterate 评审/修复闭环的专用 agent harness：
多维度代码评审反复执行直到发现**收敛**，确定性聚合，每轮验证的原子修复，
以及全程可审计的 append-only 决策日志。

它是 [OpenHarness](https://github.com/HKUDS/OpenHarness)（v0.1.9，MIT）的
聚焦型 fork：内核 agent loop、React TUI、工具/技能/插件体系与权限层全部
继承；在此之上叠加了从 iterate skill TypeScript 实现移植的语义层，以及
引擎级的收敛控制策略。

<p align="center">
  <a href="#-快速开始"><img src="https://img.shields.io/badge/快速开始-5_分钟-blue?style=for-the-badge" alt="Quick Start"></a>
  <a href="#-iterate-特性"><img src="https://img.shields.io/badge/Iterate-6_工具-ff69b4?style=for-the-badge" alt="Iterate Tools"></a>
  <a href="#-iterate-特性"><img src="https://img.shields.io/badge/模式-dry--run_|_normal-61DAFB?style=for-the-badge" alt="Modes"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/License-MIT-yellow?style=for-the-badge" alt="License"></a>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/python-≥3.10-blue?logo=python&logoColor=white" alt="Python">
  <img src="https://img.shields.io/badge/React+Ink-TUI-61DAFB?logo=react&logoColor=white" alt="React">
  <img src="https://img.shields.io/badge/version-1.2.0-brightgreen" alt="Version">
</p>

---

## 🚀 快速开始

```bash
# 一键安装（macOS / Linux / WSL）
curl -fsSL https://raw.githubusercontent.com/jingzhao-l/iterate-harness/main/scripts/install.sh | bash

# 启动 TUI
oh

# REPL 内
/iterate review        # dry-run：只读多轮评审，直到收敛
```

也可以走 CLI：

```bash
oh iterate init          # 检测项目，生成 iterate.config.yaml
oh iterate review        # 无头 dry-run（支持 stream-json 输出）
oh iterate review --changed # 快审：只审相对 --ref（默认 HEAD）改动的文件
oh iterate run           # 无头自治修复闭环
oh iterate resume        # 恢复上次会话
oh iterate log           # 查看决策日志尾部
oh iterate log --trend   # 查看跨运行 finding 趋势（新增/已修复/回归/顽固）
oh iterate log --replay  # 按时间序回放整次运行（相对时间戳）
oh iterate report        # 渲染最终报告（CI 模式，见下）
oh iterate report --html # 单文件 HTML 报告（收敛曲线、内嵌 diff、可直接分享）
oh iterate batch a/ b/   # 顺序评审多个仓库，按严重度加权排出最差榜
oh iterate schedule add "0 9 * * 1-5" # 每日 changed-only 快审（cron，UTC）
oh iterate hook install  # 托管 pre-commit 钩子：1 轮 changed-only 提交门禁
```

先设置 API Key：`export ANTHROPIC_API_KEY=your_key`（也支持
OpenAI 兼容供应商，见 `oh --help`）。

## ✨ Iterate 特性

| 能力 | 说明 |
| --- | --- |
| **确定性评审引擎** | `iterate_review` plan / aggregate / meta-review：跨轮去重、`known_intentional` 过滤、severity 排序、收敛统计、6 项报告一致性审计——全部纯计算，零 LLM 判断 |
| **两种模式** | `dry-run`（纯评审，绝不改文件）与 `normal`（评审 → 原子修复 → 验证 → 循环，验证失败经 git 隔离自动回滚） |
| **引擎级收敛强制** | `IterateLoopPolicy` 位于内核查询循环：轮次上限、收敛自停、下一轮引导不受 prompt 注入影响 |
| **收敛仪表盘** | React TUI 实时面板：逐轮 findings 趋势、维度分布、累计 USD 成本、收敛徽标 |
| **findings 分诊** | `iterate_triage`：逐条 `y` 修复 / `n` 跳过 / `a` 永久忽略；`a` 持久化到 `known_intentional`，后续轮次自动过滤 |
| **成本透明** | token 用量按内置价格表换算为每轮/累计 USD（可按模型覆盖） |
| **安全边界代码化** | 设置中的 `protected_paths` 与 `forbidden_fix_patterns` 自动装配进权限层（deny 路径规则 + 写载荷正则）；验证命令走精确匹配白名单 |
| **逐修复 diff 审批** | `require_fix_approval` 让 normal 模式闭环中的每次文件写入都走带 diff 预览的交互确认——即便处于全自动模式；硬拒绝（保护路径/禁止模式）绝不降级为可确认 |
| **Esc 中途干预** | 闭环运行中按 Esc：在下一轮边界暂停并弹出方向键菜单（跳过当前 finding / 收窄维度 / 直接停 / 继续）；再按一次 Esc 强制打断当前 turn |
| **finding 指纹趋势库** | 每次收尾用 `文件\|行号\|维度` 指纹把 finding 记入 `.iterate/trend-library.json`；`oh iterate log --trend` / `/iterate trend` 跨运行统计新增 / 已修复 / 回归 / 顽固（连续 3+ 轮）finding |
| **断点续跑** | TUI 启动画面汇总上一次收尾（结论、轮数、严重度分布、最后一次干预），`/iterate resume` 基于决策日志续跑并复核仍然复现的 finding |
| **CI / PR 模式** | `oh iterate report --github --fail-on high` 将最终报告转为 GitHub Actions 批注，并按严重度门禁决定退出码 |
| **changed-only 快审** | `--changed [--ref <ref>]`（CLI 与 `/iterate review --changed`）把整个闭环钉在 git 增量上：kickoff、评审计划与每个维度 reviewer prompt 都携带明确的改动文件清单 |
| **批量排行** | `oh iterate batch repoA repoB …` 顺序评审多个仓库，按严重度加权排出最差榜；单个仓库失败不会中断整批 |
| **定时评审** | `oh iterate schedule add "0 9 * * 1-5"` 注册 cron 任务，每日（UTC）自动跑 changed-only 快审（`--clean-ok`）；新增 vs 顽固 finding 经趋势库呈现 |
| **HTML 单文件报告** | `oh iterate report --html` 把整次运行渲染成一个可离线打开的 `.html`：SVG 收敛曲线、severity/维度分布条、含失败场景的 findings 表、按修复着色的 diff——可直接作为 CI 产物分享 |
| **评审回放** | `oh iterate log --replay` 按时间序回放整次运行（`[+90s] r1 review_result newFindings=3`），像看录像一样还原闭环展开过程 |
| **per-dimension 资源** | `iterate.config.yaml` 的 `dimension_resources` 支持按维度设置 `model` / `concurrency`（1–8）/ `token_budget`——security 用强模型、style-tests 用快模型；评审计划会携带到每次 reviewer 派发 |
| **token 预算强制** | `token_budget` 在引擎层封顶整轮运行（超限硬停并转入收尾报告）；`iterate_review(operation="aggregate", dimension_usage=…)` 审计各维度用量，把 reviewer 上报的累计 token 回传引擎成本表，下一轮自动跳过已超限维度 |
| **阈值门禁** | `thresholds.max_critical` / `max_high`（全局或按维度）封顶最终报告中的发现数量——违规即把结论翻转为 `needs_revision`，并让 `oh iterate report` 退出码失败（`threshold gate: FAIL`） |
| **定时评审时区** | `oh iterate schedule add "0 9 * * 1-5" --timezone Asia/Shanghai` 按本地时区解释 cron（存储为 UTC 标准化时间），"每天 9 点"就是你所在地的 9 点 |
| **检测式 init** | `oh iterate init` 探测项目标记文件（package.json / pyproject / go.mod / Cargo.toml / …），基于真实证据推断测试命令，推荐评审维度（前端依赖解锁 `frontend-backend` / `ui-ux`），预览 yaml 并确认后才写入；TUI 内 `/iterate init` 同效 |
| **pre-commit 钩子** | `oh iterate hook install` 写入带标记的托管 `.git/hooks/pre-commit`：提交前跑 1 轮 changed-only 评审并按 `--fail-on` 严重度门禁；拒绝覆盖第三方钩子，可用 `ITERATE_SKIP_HOOK=1` / `--no-verify` 跳过 |
| **决策日志** | append-only `.iterate/decision-log.jsonl`：每轮、每次修复、验证与分诊决策全部落盘 |
| **项目知识** | `ITERATE.md` 项目知识 + 按项目隔离的 9 类结构化个性化数据 |

## 🔧 六个 iterate 工具

- `iterate_config` — 生效配置（默认值 + `iterate.config.yaml` 覆盖）
- `iterate_validate` — 运行预配置验证命令（仅精确匹配）
- `iterate_review` — 确定性引擎：plan / aggregate / meta-review
- `iterate_decision_log` — append-only 决策日志
- `iterate_context` — SKILL.md / ITERATE.md / 个性化上下文
- `iterate_triage` — 交互式 y/n/a findings 分诊，`a` 持久化 known_intentional

`/iterate` 斜杠命令（status / review / run / log / config / validate）与
内置 `iterate` skill 提供同样闭环的不同入口。

## 🧭 架构

```
src/openharness/
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
├── engine/             # 内核 agent loop（上游 + iterate 控制块）
├── permissions/        # 权限检查 + iterate 自动装配（protected_paths…）
├── tools/iterate_tools.py  # 六个 iterate_* 工具
└── ui/                 # React TUI 后端宿主 + review_progress 协议
```

## 📦 安装

- **macOS / Linux / WSL**：`bash scripts/install.sh`（克隆 + venv + 可编辑
  安装，把 `oh` 与 `iterate-harness` 链入 `~/.local/bin`）
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

MIT——与上游一致。iterate-harness 是
[OpenHarness](https://github.com/HKUDS/OpenHarness) 的 fork，维护于
[jingzhao-l/iterate-harness](https://github.com/jingzhao-l/iterate-harness)；
agent 内核、TUI 与扩展体系的全部功劳归于上游。iterate 语义层源自
[iterate-skill](https://github.com/jingzhao-l/iterate-skill) 项目。
