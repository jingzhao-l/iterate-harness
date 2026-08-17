# Contributing to iterate-harness

iterate-harness is an open-source agent harness for the iterate review/fix loop
by default — clear, hackable, and compatible across a wide range of AI coding
workflows. It is a focused fork of
[OpenHarness](https://github.com/HKUDS/OpenHarness) that layers the iterate
semantic layer (deterministic review engine, engine-enforced convergence,
append-only decision log, git-isolated fix loop) on top of the upstream agent
kernel and TUI.

## Ways to contribute

- Fix bugs or tighten edge-case handling in the harness runtime
  (`src/iterate_harness/engine/`, `src/iterate_harness/iterate/`, …).
- Improve docs, onboarding, examples, and architecture notes.
- Add tests for tools, permissions, the iterate semantic layer, or the TUI.
- Contribute new skills, providers, or provider compatibility improvements.
- Share real usage patterns that can be added to
  [`docs/SHOWCASE.md`](docs/SHOWCASE.md).

## Repository layout

iterate-harness is **developed and reviewed in the
[iterate-skill monorepo](https://github.com/jingzhao-l/iterate-skill)** at
`harness/iterate-harness/`, and **published through its own release repo**
[jingzhao-l/iterate-harness](https://github.com/jingzhao-l/iterate-harness).
Each release splits the `harness/iterate-harness/` subtree into the release repo
via `git subtree`, then publishes the Python package and the npm wrapper from
there. The monorepo remains the single point of development and review.

```text
src/iterate_harness/
├── iterate/            # semantic layer (dedupe / severity / convergence / decision log …)
├── engine/             # kernel agent loop (upstream + iterate control block)
├── permissions/        # permission checker + iterate auto-assembly
├── tools/iterate_tools.py  # the six iterate_* tools
└── ui/                 # React TUI backend host + review_progress protocol
```

## Development setup

Requirements: **Python ≥ 3.10**；Node.js ≥ 18（启用 React TUI 与 npm 包装器测试）。

```bash
git clone https://github.com/jingzhao-l/iterate-skill.git
cd iterate-skill/harness/iterate-harness

# 创建虚拟环境并安装 editable 依赖（或直接用 scripts/install_dev.sh）
python3 -m venv .venv && source .venv/bin/activate
pip install -e .
```

也可以直接从本地检出安装 CLI：

```bash
bash scripts/install_dev.sh
```

如果你要跑/改 React TUI 相关测试，还需要 Node.js ≥ 18；TUI 的前端依赖会在
首次启动时由 harness 自动安装。

## Local checks

本地验证通过后再提交 PR（与 CI 一致）：

```bash
# 语义层 + 内核集成测试
python -m pytest tests/test_iterate -q

# 全量测试套件
python -m pytest -q

# npm 包装器引导测试
cd npm
node --test test/bootstrap.test.js
```

## Pull request expectations

- Keep PRs scoped. Small, reviewable changes merge faster than broad rewrites.
- Include the problem, the change, and how you verified it（附上具体验证命令输出）。
- Add or update tests when behavior changes.
- Update docs when CLI flags, workflows, or compatibility claims change：
  - 中英双语 README（`README.md` / `README.zh-CN.md`）
  - CLI / 斜杠命令清单、`iterate.config.yaml` 相关章节
  - `docs/SHOWCASE.md`（新增真实使用示例时）
- Add a short entry under `Unreleased` in [`CHANGELOG.md`](CHANGELOG.md) for
  user-visible changes（新版本号在发版时统一同步）。

## Documentation and community contributions

Useful contributions in the docs area include：

- README accuracy improvements and compatibility notes.
- Short, reproducible examples for common workflows（review / run / batch / schedule
  / report --html / hook / resume …）。
- Showcase entries based on real usage rather than generic marketing claims.
- Contribution and maintenance docs that make the repo easier to navigate.

## Reporting bugs and proposing features

- Use the GitHub issue templates when possible.
- Include environment details（Python / Node 版本、系统、provider 配置）、
  精确命令与错误输出。
- 涉及模型调用、网络、TLS、断点续跑的错误，请参考 `README.md` 的「故障排查 / 常见失败自愈指南」章节，并注明已尝试的修复。
- For features, explain the concrete workflow gap and expected behavior.
- If the request is mostly documentation or maintenance related, say that
  explicitly so it can be scoped as a docs PR.

## License

iterate-harness 采用 [MIT 许可](./LICENSE)，与上游
[OpenHarness](https://github.com/HKUDS/OpenHarness) 一致。iterate 语义层源自
[iterate-skill](https://github.com/jingzhao-l/iterate-skill)。通过提交 PR，
你同意你的贡献采用相同的 MIT 许可。