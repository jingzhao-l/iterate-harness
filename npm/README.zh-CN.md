# iterate-harness（npm 分发包装器）

<p align="center">
  <a href="README.md"><strong>English</strong></a> ·
  <a href="README.zh-CN.md"><strong>简体中文</strong></a>
</p>

[iterate-harness](https://github.com/jingzhao-l/iterate-harness) 的一行 npm 安装入口——
面向 iterate 评审/修复闭环的专用 agent harness（`ih` CLI、React TUI、
六个 `iterate_*` 工具、引擎级收敛控制）。

```bash
npm install -g iterate-harness
ih --version        # 首次运行引导安装 Python 端，随后打印版本
```

## 这个包是什么（以及不是什么）

harness 本身是**一个 Python 包**。这个 npm 包是一个薄分发包装器：它不重新实现任何
逻辑，只是让 `ih` 可以通过 npm 安装并保持更新。

`npm install` 一结束，`postinstall` 钩子就会创建托管虚拟环境并安装 harness——
所以 `ih` 装完即用。安装时与运行时共用同一套代理逻辑：

1. 找到一个 >= 3.10 的 Python 解释器（`py -3` / `python` / `python3`，
   可通过 `ITERATE_HARNESS_PYTHON` 覆盖）。
2. 在 `~/.iterate-harness-npm/venv` 创建托管虚拟环境
   （可通过 `ITERATE_HARNESS_NPM_HOME` 覆盖）。
3. `pip install` **与本 npm 包版本锁定的** harness 发布版本 —— npm 的 `1.6.0`
   总会安装 harness `v1.6.0`。升级 npm 包会自动重装对应的 harness 版本。
   优先下载 GitHub 发布里的**预构建 wheel**（`iterate_harness-<version>-py3-none-any.whl`，
   已内置编译好的前端资源——和 iterate-skill-installer 分发预打包资源一致）。
   若 wheel 缺失，再兑底使用锁定的**源码归档**。
4. 代理调用 venv 里真正的 `ih` 可执行文件，转发 argv、stdio、信号与退出码。

若 `npm install` 期间无法完成安装（无 Python、无网络，或
`ITERATE_HARNESS_SKIP_INSTALL=1`），钩子只会打印提示而不让 npm 安装失败，
`ih` 会在首次运行时补装——惰性兑底保证两种情况下包都可用。

React TUI 的前端依赖（`node_modules`）会在首次启动 TUI 时由 harness 自动安装——
npm 用户必然有 Node，所以 TUI 始终可用。

## 环境要求

- Node.js >= 16（你通过 npm 安装它，必然已具备）
- Python >= 3.10（可用 `ITERATE_HARNESS_PYTHON=/path/to/python3.12` 覆盖）
- 能访问 registry.npmjs.org（已完成）、pypi.org 和 github.com（用于一次性 pip 安装）

## 环境变量

| 变量 | 作用 |
| --- | --- |
| `ITERATE_HARNESS_PYTHON` | 使用该解释器，而非自动检测 |
| `ITERATE_HARNESS_NPM_HOME` | 运行时目录（默认 `~/.iterate-harness-npm`） |
| `ITERATE_HARNESS_INSTALL_URL` | 从这个 pip URL 安装，而非锁定的发布 wheel（例如测试用的 git 分支） |
| `ITERATE_HARNESS_SKIP_INSTALL=1` | 完全跳过引导安装，直接运行已装好的 `ih` |

## npx（免全局安装）

```bash
npx -y iterate-harness iterate review --changed
```

## 卸载

```bash
npm uninstall -g iterate-harness
rm -rf ~/.iterate-harness-npm   # 托管虚拟环境
```

## 版本管理

本包的版本与 harness 保持同步：包装器 `x.y.z` 安装 harness 的 `vx.y.z` 标签。
包装器在 [iterate-skill monorepo](https://github.com/jingzhao-l/iterate-skill) 的
`harness/iterate-harness/npm/` 维护，通过 `git subtree` 同步到
[iterate-harness 发布仓库](https://github.com/jingzhao-l/iterate-harness) 并从那里发布到 npm。

## ⚠️ 免责声明

本包按「现状」（AS IS）提供，不附带任何明示或暗示的担保，包括但不限于对适销性、特定用途适用性及不侵权性的担保。它所安装的 harness 会执行自动化的代码审查与修复，normal 模式下产生的所有改动均由 AI 模型生成，可能引入缺陷、回归或非预期行为。使用者需为本项目所产生、修改或提交的代码负全部责任。完整说明与安全指引见 [iterate-harness README](https://github.com/jingzhao-l/iterate-harness)。

## License / 许可证

MIT —— 与 harness 相同。iterate 语义层源自
[iterate-skill](https://github.com/jingzhao-l/iterate-skill)。