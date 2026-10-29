# Changelog

All notable changes to iterate-harness should be recorded in this file.

## [Unreleased]

（空。2.13.0 收口后没有未归号的条目。）

## [2.13.0] - 2026-10-29

**版本判断：minor。** 新增一个用户可达的控制台页面与一条只读 API；既有形状不动：CLI 命令、参数与退出码集合未变，cron 注册表 / 历史文件的格式未变，页面不写任何文件。定时评审从 `ih iterate schedule` 起就能被 CLI 管理，但操作台既看不到“登记了什么”，也看不到“昨晚那一跑过没过”——这一版补的是这条断链。

- **控制台第一次能看到本机的定时任务。** `GET /api/v1/schedules` 返回
  `scheduler`（是否运行、pid、启用/总数、日志与历史文件路径）、`jobs`（名称、cron 表达式、时区、
  命令、工作目录、启用状态、表达式是否有效、下次运行、上次运行与结果）与 `history`（每次执行的开始/结束、
  状态、退出码、耗时、stdout/stderr 尾部）。数据来自 `scheduler_status()`、`load_cron_jobs()` 与
  `load_history()`，与 `ih cron status|history --json` 同源。
- **“下次运行”是每次读取时重算的，不是注册表里的旧值。** 注册表里的 `next_run` 只在 daemon 跑过之后
  刷新；调度器停了一周再打开页面，旧值会是一个已经过去的时间。路由用 `next_run_time(schedule,
  tz_name=…)` 以当前时刻重算，并保留时区语义（`0 9 * * 1-5` + `Asia/Shanghai` → 01:00 UTC）；表达式
  非法时 `schedule_valid=false`、`next_run=null`，页面显示“无效表达式”而不是猜一个时间。
- **历史读取做了归一化与边界。** 旧写入器的 `jobName` 归一为 `name`；`duration_s` 由起止时间算出；
  stdout/stderr 只带尾部 2000 字符（完整输出仍在日志文件里），单次响应不会被一条巨长的构建日志撑爆。
  注册表或历史文件损坏时沿用既有 `load_*` 的降级（空列表 + warning），页面显示空态而不是 500。
- **前端新增「定时任务」页（侧栏 + `g s`）。** 四张状态卡（调度器、计划数、最近一次执行、日志/历史文件名）、
  计划表（下次运行、上次结果、命令与目录）、执行历史表（状态徽标、退出码、耗时、可展开的 stdout/stderr 尾部）。
  关键状态写明：调度器没在跑而计划已登记时，页面单独给出“计划不会自动执行”的提示与
  `ih iterate cron start`；没有计划/没有历史时给出产生数据的命令。本页属于本机（`~/.iterate-harness/data`），
  不随项目根目录切换，页面顶部明确说明。
- **文档与帮助同步。** 两份 README 的页面清单、Scheduled review 条目与 WebUI 段落都补上 Schedules/定时任务；
  `ih web serve --help` 的页面列表加入 scheduled reviews；版本号在 `__init__.py`、`npm/package.json`、
  `frontend/web/package.json` 与 lock 中同步为 2.13.0。

**核验（本机，2026-10-29）：**

- 端到端（真起进程、真令牌、真注册表/历史）：`ITERATE_DATA_DIR=<tmp> ih web serve --no-browser` 后
  `curl /api/v1/schedules` 返回 `scheduler.running=false, total_jobs=1, enabled_jobs=1`，
  `jobs[0].next_run=2026-10-30T01:00:00+00:00`（`0 9 * * 1-5` + Asia/Shanghai 的 09:00），
  `last_status=success`，`history` 两条按时间正序、`duration_s=123.0/42.5`、失败那条带
  `stderr_tail="threshold gate: FAIL"`；构建产物 bundle 能抓到「定时任务」串。
- `pytest tests/ -q` → **3149 passed, 6 skipped, 1 warning in 117.90s**（新增 6 条：空注册表、时区下次运行、
  非法表达式、历史归一化与尾部截断、损坏文件降级、limit 边界）。
- 反向变异：把 `_tail()` 的截断改成原样返回 → `test_history_is_normalised_with_duration_and_bounded_tails`
  变红；还原后 sha256 与基线逐字节相等（`64d3676d…`），复测 6 passed。
- `ruff check .` → All checks passed；`mypy src` → Success: no issues found in 271 source files；
  `node --test test/bootstrap.test.js` → 68/68 pass；前端 `tsc --noEmit` 通过、`vitest run` →
  7 files / 79 tests passed、`vite build` 成功。

## [2.12.0] - 2026-10-28

**版本判断：minor。** 这一版新增一个用户可达的控制台页面与一条只读 API，既有形状不动：CLI 命令、参数与退出码集合未变，`requires-python >= 3.10` 未变，决策日志与 `recipeStepGate` 行的格式未变，页面也不写 `.iterate`。判定闸的答案自 GlassPane 交接落地起就能被 `ih iterate gate` 从账本里读出来，但控制台读不到——这一版补的就是这条断链，因此是净新增能力而不是修补。

- **控制台第一次能读到 GlassPane 闸的答案。** `GET /api/v1/gate` 返回
  `has_gate / total / latest / history / rendered`；`latest` 与 `history` 是同一批记录的结构化字段
  （轮次、时间、allowed、不变量判定、配方摘要、步骤、修复证据、拒绝码/说明/补救），`rendered` 是
  `ih iterate gate` 的逐字输出。前端新增「判定闸」页（侧栏 + `g j` 快捷键）：最近一次结论、拒绝原因与
  可执行的下一步、修复证据来源、历史表逐行点选，以及一键复制终端原文。
  两个读者共用 `glasspane_recipe._published_gates`：CLI 的 `render_gate_state` 与新的
  `gate_history` 从同一次 lenient 扫描取数，所以页面与终端不可能对同一轮给出不同答案；测试直接断言
  `payload["rendered"] == render_gate_state(root)`，并有一条测试把 CLI 文本逐字钉死（多行断言）。
- **没有记录就显示“没有记录”，并且写清它不等于通过。** `has_gate=false` 只在账本里一条 `recipeStepGate`
  都没有时出现；空状态页明确写「没有记录代表没人问过，不代表通过」，并给出产生记录的命令
  （`ih iterate run --rounds 3`）与终端读取方式（`ih iterate gate`）。这与
  `render_gate_state` 一直以来的口径一致：absence is not a pass。
- **只读、可安全留在后台。** 路由只调用 `gate_history` / `render_gate_state`，不 append 账本；`limit`
  （1–500）只裁剪 `history`，`latest` 仍取全量最后一条，`total` 仍是全量计数。根目录校验与其它路由
  相同（`allowed_roots` / `default_root`，404/403 与既有页面一致），链断裂不会让页面空白——它沿用
  `decision_log.read_entries` 的宽松投影，这正是 `render_gate_state` 原本的取舍。
- **文档与帮助同步。** 两份 README 的页面清单与 GlassPane 段落都补上「判定闸 / Gate」及 `g j`；
  `ih web serve --help` 的页面列表加入 recipe gate；版本号在 `__init__.py`、`npm/package.json`、
  `frontend/web/package.json` 与 lock 中同步为 2.12.0。

**核验（本机，2026-10-28）：**

- 端到端（真起进程、真令牌、真账本）：`ih web serve --project <tmp> --no-browser --port 8792` 后
  `curl /api/v1/gate` 返回 `has_gate: true, total: 1, latest.round: 2, latest.allowed: false,
  latest.refusal.code: RECIPE_GATE_E_INVARIANT_GATE`，`rendered` 与 CLI 文本一致；同一个服务对空项目
  返回 `has_gate: false, total: 0, latest: null`，`rendered` 以
  `no recipe gate recorded yet … that is absence, not a pass` 起头。构建产物里能抓到页面标题串
  （`curl /assets/index-*.js | grep -c 判定闸` → `2`）。
- `pytest tests/ -q` → **3143 passed, 6 skipped, 1 warning in 111.70s**（新增 11 条：路由 6 条 +
  `gate_history` 5 条；含 403/404、limit 边界、畸形 payload、CLI 文本逐字）。
- 反向变异：把 `gate_history` 的 `allowed=bool(...)` 改成 `allowed=True` → 3 条具名测试变红
  （`test_latest_answer_and_history_order`、`test_records_are_oldest_first_and_carry_the_published_fields`、
  `test_a_malformed_payload_is_reported_rather_than_raised`）；还原后 sha256 与基线逐字节相等
  （`daaa8da5…`），同两条文件 11 passed。
- `ruff check .` → All checks passed；`mypy src` → Success: no issues found in 270 source files；
  `node --test test/bootstrap.test.js`（npm 包装器）→ 68/68 pass；前端 `tsc --noEmit` 通过、
  `vitest run` → 6 files / 69 tests passed、`vite build` 成功（dist 里含新页面）。

## [2.11.2] - 2026-10-26

**版本判断：patch。** 这一版不加任何用户可见能力，也不动既有形状：`requires-python >= 3.10` 未变、CLI 参数与退出码集合未变、内核仍是 `iterate-kernel@0.1.10`、`.iterate/decision-log.jsonl` 与 `recipe-gate` 行的格式未变。变的是两件「已经宣称过、但今天才成立」的事：① `cron_scheduler` 不再对「pid 文件里的号」开枪——它现在要求那个号仍是写凭证的进程，且 `killpg` 只准打本任务用 `start_new_session` 自己造的组；② `Python tests` 那条 lane 从「12 条里 10 条绿、2 条永不结束」变成 **12/12 绿**。附带一条形状变化要说清：pid 文件从裸整数变成 `{"pid", "cmdline"}`。新 CLI 读旧文件（`_DAEMON_ENTRY_CODE` 兜底，实测停得住旧 daemon）；**旧 CLI 读新文件会把它当成「没有 daemon」**（`int()` 解析失败→ None），也就是先升级 daemon、再退回旧 CLI 的组合下 stop 会失灵——同一个包里两边同源，正常升级路径碰不到，降级会碰到，因此记在这里而不是藏在代码注释里。既有形状零破坏 → 不是 major；新增能力零 → 不是 minor。

- **`bucket 06` 那条挂了两天的腿，名字量出来了，而且名字后面跟着一条真缺陷：一段测试代码让 SIGKILL
  走进了 `kill(-1, SIGKILL)` 那条广播语义。**
  判据不是再加仪器，而是把可疑那一桶拆成"一个模块一条腿"（新的 `suite-bisect.yml`：只能手动派发、
  不进任何判定、矩阵由 CI 自己的 plan 现算）。run 40249882415 的读数是 **32 条腿里 31 条绿，唯一不
  结束的是 `tests/test_services/test_cron_scheduler.py`**，而它也是唯一没留下日志 artifact 的那条。
  同一批读数顺手推翻我自己写在 README 里的两句：`Collect this bucket and check it is the bucket the
  plan promised` 在那些路径上 8 s 完成（23:23:04→23:23:12）⇒ 挂的**不是收集期**；绿兄弟腿的
  `Run test suite` 只花 23 s（23:23:15→23:23:38，520 条）⇒ 也不是"这些用例同进程本来就慢"。
  机制在本机量到两处，都在 `src/iterate_harness/services/cron_scheduler.py`：
  ① `_kill_process_group()` 无条件 `os.killpg(os.getpgid(process.pid), SIGKILL)`，而
  `TestExecuteJob::test_timeout_job` 递给它的是一个 AsyncMock——本机实测 `AsyncMock().pid.__index__()`
  **返回 1**，Linux 上 `killpg(1, …)` 就是 `kill(-1, …)`，而 kill(2) 对 `pid == -1` 写明的语义是"把信号
  发给调用者有权发给的每一个进程"：同一 uid 下的 `Runner.Worker`、步骤的 shell、actions 的 node 全在射程
  内，并且只要打中一个系统调用就返回 0，看起来像成功。这一步的 `except (ProcessLookupError,
  PermissionError, OSError)` 因此接不到任何东西。本机这一半是量下来的另一副样子：`getpgid(1)` 回 1
  （拿得到），而 `killpg(1, 0)` 回 EPERM——旧代码正好把 EPERM 吞掉退回 `process.kill()`，所以同一行在
  macOS 上从未炸过，这就是它只在 ubuntu 上发生的理由。Linux 那一半依据的是 kill(2) 的成文语义，本机
  没法实测；因果是否闭合，判决交给 CI 复跑，这一段只写"已修的代码路径＋量到的两半"。
  ② `read_pid()`/`stop_scheduler()` 只问"这个号活着吗"（`os.kill(pid, 0)`），从不问"这个号是不是本
  daemon"。本机对照实验（`/var/tmp/prove_pid_claim.py`，一个从没见过这个 daemon 的活 `sleep` 子进程被
  写进 pid 文件）：**修前** `stop_scheduler()` 在 13.5 s 里先 SIGTERM 再 SIGKILL 把它打死（退出码
  `-15`），事后还报"survived SIGKILL"并留着 pid 文件——僵尸也答 signal 0；同一个函数对**真 daemon**
  也返回 False，而那 daemon 其实已退 0。**修后** 陌生进程毫发无损（0.0 s 拒绝并释放凭证），真 daemon
  0.2 s 停住并如实返回 True。pid 会被回收再发给别人，而文件里过去只有一个号。
  修：pid 文件从"一个号"改成"号 + 写它的进程当时的 command line"的凭证；`read_pid()` 先验活（僵尸算
  已死）再验连续；比对取 argv 尾——量到解释器会在 exec 之后重写 argv[0]（同一个子进程先答
  `.venv/bin/python -c …`、随后答 framework 的真实路径），整行比对会拒绝一个从未变过的好凭证，第一版
  就是这么把 7 条原本绿的用例打红的；读不到命令行时（Windows）如实接受并在日志里说明，不假装验过；
  旧版裸号文件由 `_DAEMON_ENTRY_CODE` 兜底，升级路上旧 daemon 仍停得住。`killpg` 从此只打
  `getpgid(pid)==pid` 的、也就是自己用 `start_new_session` 造出来的组，pid 不是可用整数的一律不打。
  核验：4 项反向变异各自点名变红（M1 抽掉身份核验→3 条具名红；M2 僵尸算活着→那条红；M3 回到没有归属
  proof 的 `killpg`→那条红；M4 `write_pid` 退回裸号→新加的"真 daemon 必须写下指纹"那条红），变异用
  备份＋sha256 核回（`b54a2daa…`，跑完与基线逐字节相等）；本模块 40 passed；全量本机
  3126 passed / 6 skipped / 6 failed，那 6 条在**未改动的树上同名同序地红、单跑也红**（已知环境组，
  不是本批造成的）。同一个 stub 在修后再也打不出 `killpg`：实测 `after the fix, killpg calls: none`。
  这一格唯一还没证的事是 CI 自己：修前它是"31 绿 1 永不结束"，修后应 12 条全绿。在那之前本版不写
  "CI 绿"，2.11.2 也不取。
  **CI 已经答完了，答案是绿的。** run 40252896348（standalone main `e94ded57`）：`Python tests` 12 条腿
  **12/12 completed/success**，`artifacts.total_count=13`（tarball＋12 份腿日志），run 结论
  `completed success`。`bucket 06` 那条腿自己写下的最后三行就是这一格的终态：
  `suite_pid=2425 session=2425`（setsid 确实脱离了）、`recorder ref deleted (the artifact carries the log)`
  （步骤结束得干净，连记录器的诊断 ref 都按规矩删了）、
  `pytest_exit=0 stopped=finished elapsed=38s`，日志末尾 **518 passed, 5 skipped in 32.10s**（3.11 那条是
  25.45 s）。同一 run 的 plan 报 `3138 test(s) in 6 buckets`。本仓的 CI 绿，从这一笔起才有资格写。


- **改 CI 注释时我自己把 `ci.yml` 的一个 step 写出了两个 `run:` 键，而 PyYAML 不报错——它取最后一
  个。** 于是那一步实际跑的是旧的单行命令，上面新加的 `timeout` 硬停块只是**看起来**像交付了的
  行为。这条被 `tests/test_ci_workflow_shape.py` 抓住（新文件，8 条），它判重的地方是
  **compose 出来的节点树**而不是 parse 出来的 dict——dict 里重复键早就没了，`set(keys)` 数一遍是
  假绿（这正是本仓上一次"用 YAML 解析器数键查重复＝假绿"的同一个坑）。四条反向变异各点名变红：
  重插重复 `run:` 键 → 三条判据全红；抽掉 `timeout` → 那条"这一步还带着哪些探针"的红；
  把多行 `run:` 的 `set -euo pipefail` 去掉 → pipefail 那条红；把收集步改名让它脱离前缀 → 收集步
  那条红。变异用备份 + sha256 核回（`0d536303…`），battery_rc=0。
  顺手把同一个文件里两处 `npm ci` / `npm run build` 的多行体补上 `set -euo pipefail`（父仓的
  `check-workflows` 就是这条规则，本仓此前没人管）。
- **那条 45 分钟那一格的第二次仪表也没能救它，于是两条简单解释都被量掉了。**
  `timeout-minutes: 90` 是我这一轮加的，job 仍在 **45:01** 结束（40179763746 09:13:44→09:58:45），
  两条 matrix 同一秒——所以切断不是本工作流的设置，也不是某条用例里的等待；`-v` 那一版照样死，
  所以"通道沉默"也不是原因。本机另两个读数把剩下两条也容易解释掉了：**这套并不慢**（空闲
  `pytest -q tests` = **115.59s** / 3089 条收集，6 failed 是已知环境组）、**也并不吃内存**
  （`/usr/bin/time -l` 报 maximum resident set **286,605,312 字节** ≈ 273 MB，对面是 7 GB 的
  runner，OOM 说不过去）。剩下的工作是那条新加的 `timeout --kill-after=60 1200` 硬停探针该给出的
  三种读数哪一种（测试跑完了卡在退出＝日志底部会有 summary；某条用例卡住＝名单断在中途；
  仍在 45:00 整断＝上限在外面，那就得把这步按目录拆）。判据与禁忌都写在 step 注释里。
- **两次探针都跑完了，谁也没把机制定下来——于是换一件能"离开机器"的仪器。**
  run 40184095706 里这一步跑到 **27 分钟仍在继续**（比它自己 1200 s 的上限晚 7 分钟，
  `--kill-after=60` 早过完）；紧接着 run 40187109237 把"pytest 的输出去到文件、不再占着 Actions
  管道"这一版也试了，**27 分钟仍然报 `in_progress`**。诚实的读法是：一个过了自身上限还 `in_progress`
  的步骤，与两种状态都相容——(i) 步骤等的某个东西活过了信号（本套件确实有五处用
  `start_new_session=True` 起孩子），或 (ii) **runner 已经不报了，API 展示的是陈旧状态**，
  要等 45 分钟"失联"那一刀才落地。站在外面**没有任何可观测量能把这两者分开**，
  而两者都会把日志一起带走。所以基于 flag 的理论到此用尽：缺的不是又一个 flag，
  是一个**跑得出去**的观测。于是加 `actions/upload-artifact` + `if: always()` 把
  `pytest-ci.log` 传出来——它是这个 job 里唯一一条"输出比 runner 活得久"的通道；
  拿到它的第一行（是 "test session starts" 之后就没下文，还是逐条名单断在某处）就是所有人
  还没做过的那一次观测。判据也进了 `tests/test_ci_workflow_shape.py`：那一步必须还在、
  必须挂 `always()`。
- **硬停本身也要有对照。** 连着两次"上限过了还 `in_progress`"的唯一解释前提是**那把上限真的会落地**，
  而这句话此前没人验过。于是加一步 5 秒控制：`timeout 5 sleep 30`，回显自己的退出码与秒数并断言
  `124` 与 `<=10s`；它排在套件硬停之前，专问"这是不是一把刀"。本机量不了这件事——`timeout` 与
  `gtimeout` 在这台机器上都不存在（正是以前本地脚本用不了 `timeout` 的同一个坑），所以只能在 runner
  上观察，也因此它必须把自己的码与秒数打出来。`tests/test_ci_workflow_shape.py` 现在 10 条：控制必须
  存在、必须排在被它认证的步骤之前、必须用与套件无关的命令、必须回显 exit code。

- **`/iterate` 不带参数打印的是这个项目当前状态，不是子命令清单。** 上一轮我为了"抄来的清单每加一条
  子命令就漂一次"把 README 与注册表描述改成"不带参数会打印当前清单"——这句本身也是一句抄来的话，
  实测是错的：裸命令走 `status` 分支（配置摘要 + 决策日志条数 + 引导提示），**打印清单的是不认识的
  子命令**。现在两处 README、`registry.py` 的命令描述都按实测改写，并新增
  `tests/test_iterate/test_iterate_slash_surface.py`：把 README 里那份 16 项清单与 handler 的分支
  条件逐字比相等（两个语言各一条），两个入口各跑一次真 handler 判形状。控制实测：把注册表描述改回
  旧句、把中文清单里的一项改名、把裸命令改派到未知分支，三种都各自具名红。

- **跨项目验收 `scripts/check_run_plan_channel.py` 修好了一件它此前一直在隐瞒的事：默认调用
  从来没有测量过任何东西。** 两个默认值各多了一段目录——
  `REPO.parents[1] / "iterate-skill" / "config" / "iterate.config.yaml"` 实测解析成
  `/Volumes/Eng-Dev/iterate-skill/iterate-skill/config/iterate.config.yaml`，
  `…/iterate-skill/GlassPane/harness/glasspane-harness` 同理，两者的 `exists()` 都是 `False`——
  所以不带 `--config/--harness` 的默认运行在写出计划之前就 `return 2`。退出 2 的意思是
  "没有测量发生"，不是"测量通过"，而它被当成后者引用过。现在按仓库真实布局逐候选发现路径，
  发现不到就具名拒绝并退出 2。
  同时补上本版才交付的第二条通道：`.iterate/run-plan.json` 项目文件通道（`readRunPlan({}, dir)`
  而不是只喂 `GLASSPANE_RUN_PLAN`），并断言回包的 `plan.source` 等于所测的那条通道
  （`env` / `project`）——否则两条通道可以实际读同一份环境变量，却都被记成"通过"。
  实测读数：9 个维度、digest
  `rp_68049dadeff6c85c4807448b04f44462c24f069b8936e57b181a667b8f42380e`、两条通道各自
  `channel agrees`、退出 0。

- **主干 CI 的测试套件那一格：四次同死法，两版仪表，一个当场量到的时长。** 死法是先跑起来再无输出，
  job 在起跑后第 45 分钟整被 "The hosted runner lost communication with the server" 打掉，而 runner 一死
  日志就不上传（`--log-failed` 报 `log not found`，`runs/{id}/logs` 的归档里只有三条成功的 job）——
  run 40167475388、40168346235、40103418285（v2.10.0）与 40171467085。**第一版仪表不够**：只加了
  pytest 自带的 `faulthandler`（`-o faulthandler_timeout=300 -o faulthandler_exit_on_timeout=true`，
  零新依赖；对本机一条故意 `sleep(60)` 的用例实测 rc=1 并报出
  `File "…/test_hang_probe.py", line 3 in test_hangs`），而带上它的 run 40171467085 仍以同一姿势死在
  同一分钟，日志照旧没有。于是补第二版，并同时记下为什么不能断言"这是挂起"：本机跑同一套是
  **36m27s / 3083 条**——45 分钟的沉默同样可以是"这套本来就比 runner 的沉默窗口慢"，不是证据指向挂起。
  第二版做三件事，两件同时服务于两种成因：① 独立一步 `pytest --collect-only -q`（收集期就卡住＝某个
  模块在 import 时阻塞，而那正是 `faulthandler_timeout` 看不见的地方）；② 跑测试改用 `-v`（逐条打一行，
  日志通道不再沉默——慢的套会跑完并报告，真挂的会留下最后那条名字）；③ job 级 `timeout-minutes: 90`
  （有上限才会**带着日志结束**，而不是让 runner 把证据带走）。90 不是猜的，是按上面那个实测留的余量。
  判据写在步骤注释里：红了按 dump 或最后一条 `-v` 找出等的是谁；不许抬 `faulthandler_timeout` 让红消失、
  不许改成 skip、更不许为了"日志太长"把 `-v` 拿掉——那行逐条输出正是这一步可寻的原因。

- **那一格不是被第五个 flag 解开的，是被"把套件拆成会结束的桶"解开的。** run 40190097615 一次给出
  三条读数，其中两条把先前的解释判死：① 5 秒上限对照 rc=124、11:44:23→11:44:28——那把
  `timeout --kill-after=60 1200` 的刀确实会落地；② 同 run 里 `Run test suite` 从约 11:44:41 一路
  `in_progress` 到 12:29 那一刀，越过它自己的上限 25 分钟，而 `actions/runs/40190097615/artifacts`
  的 `total_count` = **0**——`if: always()` 的 artifact 步骤排在后面，前面那步永不结束它就轮不到；
  ③ 同一 run 里**已完成**的 `Python quality` job 用 `actions/jobs/121159060680/logs` 取到
  **31,956 字节**日志，进行中的测试腿 `actions/jobs/121159060770/logs` 回 **HTTP 404**。所以
  "runner 死了没上传"这句要换成更硬的一条：**job 的日志只在 job 结束之后才存在**——一个永不结束的
  step 意味着日志在任何地方都不存在，与 pytest 缓冲、与"管道被谁占着"都无关。
  另收回一句写进过注释的解释："account/plan 侧的 45 分钟上限"——
  `gh repo view jingzhao-l/iterate-harness --json visibility` 回 **PUBLIC**，公开仓没有配额上限。
  新形状：`suite-plan` 收集全量、`scripts/split_suite.py` 按测试模块做 FFD 切 6 桶，`python-tests`
  变成 `python-version × bucket` 的 matrix；每桶独立机器、独立 30 分钟、按桶命名的 artifact。
  `uv sync` 需要的 `frontend/web/dist` 只 build 一次、tar 一份、十二个桶各取一次。
  拆分自己带进来的失效形状是"绿在一个子集上"，于是三道闸写在代码里而不是散文里：脚本要求 pytest
  自己打印的 `N tests collected` 与解析到的 node id 数相等（`SPLIT_E_COUNT_MISMATCH`）、每桶跑之前
  重新收集并用 `--expect-total` 对上计划数（`SPLIT_E_PLAN_MISMATCH`）、拒绝桶数多于模块数（空桶＝
  一条什么都不测的绿色 job）。
  本机等价性实测（同一棵树）：单体 `3106 passed, 6 skipped, 1 warning in 141.89s`；六桶
  519/519/519/519/518/518 = 3,112，合起来同样的 3,106 passed + 6 skipped，六次 `--expect-total` 全
  rc=0。CI 侧 run 40195913940 落地：`Plan the suite split` 绿，`pytest-ci-log-3.11-01` 这份 artifact
  57,088 字节（zip 后 9,990），逐条名单跑到 `[100%]` 并写明 `518 passed, 1 skipped in 11.75s`——
  也就是说**这套在 runner 上也不慢**，"2 核共享 runner 上大概率超过 45 分钟"那句同样被这条直接证据
  推翻。（当时我据此写下"六个桶 12 秒跑完"，这句在本轮末尾被自己修正：十条腿确实一两分钟，
  另两条没有——见下面那条。）

- **拆完桶，挂在哪里第一次有了名字；然后把那一步改成"一定结束"的形状。** run 40195913940 的完整读数：
  `Plan the suite split` 绿，**12 条腿里 10 条绿**（3.10 与 3.11 各 5 条，每条一分钟左右），而
  `Python tests (3.10 / bucket 06)` 与 `(3.11 / bucket 06)` 从 13:04:40 起卡在 `Run test suite`，
  越过自己 1200 s + `--kill-after=60` 之后照旧 `in_progress`（13:26:50 复核），最后由 GitHub 在
  **13:39:40** 判成 cancelled，`artifacts` 停在 **11** 件——那两条腿的日志又一次不存在。
  于是"挂起"第一次落成一个**具名文件集**：bucket 06 那 31 个模块
  （`tests/test_services/test_docker_backend_stop.py`、`tests/test_ui/test_react_backend.py`、
  `tests/test_swarm/test_worktree.py`、`tests/test_services/test_cron_scheduler.py`、
  `tests/test_voice/test_voice_mode.py` 等），本机因缺 docker/node 把这些路径全跳过 ⇒ **本机不可复现**。
  形状因此再改一次：`Run test suite` 不再用 `timeout ... | tee`，而是 `setsid` 完全脱离、stdio 只进文件、
  bash 用**有界轮询**而不是等管道，到点 `kill -9 -- -$pid` 打整个新会话组，并在杀死前后各写一份
  `ps -eo pid,ppid,pgid,sess,stat,etime,args --ww`。理由正是这次量到的机制：`tee` 要等 pytest 的
  stdout 出现 EOF，而本套件五处用 `start_new_session=True` 起的孩子逃出可被信号的那一组、握着写端——
  步骤于是永不结束，日志与 artifact 跟着永不存在。步骤状态与退出码分开报（`pytest_exit=$rc
  stopped=$stopped`），"跑完而红"与"预算耗尽"从此是两种读数。前置一条与套件无关的对照，先证明
  `setsid` 与 `ps -o sess=` 在这块 image 上真的会落地、且 detached 的进程真的是自己的会话组长
  ——不是组长的话 `kill -9 -- -PID` 打的是别人的组。
  **这一步现在是被测过的程序**：`tests/test_ci_suite_step_program.py` 从 ci.yml 取出那段 bash 本体，
  用 stub 换掉 `setsid`／`uv`／`ps`／`timeout`（本机既没有 `setsid` 也没有 `timeout`；BSD 的
  `ps -o sess` 实测返回结构地址而不是 sid，所以桩必须回答那个真正要被判的性质——它以真实 pgid 作答
  "是不是组长"），跑四条：正常通过；用例红（必须是 `pytest_exit=1 stopped=finished` 而不是 124）；
  预算耗尽（必须在预算 + 轮询间隔之内自己结束，日志里带着杀死前后的 `ps`，且不留残留进程）；
  **守护必须能拒绝**（把 setsid 换成不脱离会话的启动器，步骤要在启动套件之前就停下并说出原因）。
  这几条自己又被 27 条变异电池管着：`M-T` 把 `exit "$rc"` 改成 `exit 0`、`M-U` 抽掉组杀、`M-V` 把组长
  守护改成 `if false`，、`M-W` 摘掉 `ps` 快照的 `|| true`、`M-X` 给它长回一个长选项，各自把点名的用例打红；27/27 RED-AS-SPECIFIED，变异用备份 + sha256 核回
  （`8d2b049d…` 逐字节相等）。
  电池还抓到**我刚写下的一个缺陷**：拒绝消息里用了反引号——双引号里那是命令替换，实测它执行了
  `kill`、`setsid`（无参，IndexError）与 `ps`，把一句本该解释失败原因的话跑成三条随机输出。于是新增
  `test_no_run_body_carries_a_backtick_that_is_not_a_comment`（`$(…)` 是步骤体里唯一允许的替换写法）和
  `test_every_run_body_is_syntactically_valid_bash`（每段 `run:` 过一遍 `bash -n`）。而探针判据的第一版
  本身是**假绿**的：它找 `"setsid "`，而这一步的注释与 echo 文案里也写着 setsid，于是把启动命令换成
  `timeout` 管道它照样绿——改成行首锚定 + `_commands_of()`（剥掉注释行再断言），M-B 这才红。
  本仓那句老话又应验一次：**注释不能替代码说话**。
  **而这一步第一次上 runner 就把十条本来全绿的腿打红了**，原因是我自己的仪器：快照写的
  `ps -eo … --ww` 在 runner 上答 `error: unknown gnu long option`，`set -e` 于是把十条已经打出
  `520 passed in 9.86s` 的腿判成失败（run 40199977015）。那份 `pytest-ci-log-3.11-01` 的 artifact
  57,213 字节里就是那句 passing summary——**测试这一轮第一次把结果留在了机器外面**，而它同时也是一面
  镜子：红不是它的红，是我那一步的红。两处各补 `|| true`（仪器不许变成故障本身；这条现在判据与变异各有
  一条），并记下为什么本机那五条用例当初没抓住它：**我的 `ps` 桩把一切请求吞成 exit 0**。桩改成真转发
  （同样的 argv 交给 /bin/ps、同样的退出码还回去），本机于是当场就能拒绝 `--ww`——BSD 的 ps 回
  `illegal option -- -`，与 runner 上那条 GNU 报错是同一种缺陷的两种说法。
  本机整树复跑 `3113 passed, 6 skipped, 1 warning in 123.69s`，ruff 与 mypy 绿。
  **仍未判死的是"bucket 06 那 31 个模块里谁在 ubuntu 上等"**：新形状的价值不在于它让那一格变绿，而在于
  下一次运行这一步一定会结束、一定会把日志和两份 `ps` 快照交出来——名单停在哪一行、快照里活着的是谁，
  就是那一格剩下的判据。禁忌照旧写在 step 注释里：不许抬预算、不许 skip、不许摘掉 `ps` 快照。
- **最后一步：把步骤预算收到 job 上限之下——"这一步自己能不能结束"第一次变成一次可判的运行。**
  那两条 bucket 06 腿今天被量了三遍（40199977015、40200818814），结论是同一条：job 起于 13:58:00、
  步骤预算 1200 s ⇒ 应在 14:18:30 结束，实际 **14:33:00** 才被 GitHub 判 cancelled——正好
  **35 分钟 ＝ `timeout-minutes: 30` + 约 5 分钟宽限**，artifact 仍停在 11 件。也就是说步骤内的有界轮询
  要么没被执行、要么执行了而 runner 不报，从外面看是同一张脸。于是 `PYTEST_HARD_STOP` 1200→**600**、
  job 上限 30→**12**（本机整套 123.69 s / 3,119 条，一桶约 520 条；同 run 十条腿各约一分钟且含
  checkout 与 uv sync，600 s 约是一桶实测成本的 40 倍），让下一次运行必须交出可判的读数：
  ① artifact 里有日志 ⇒ 轮询有效，名单停在哪一行就是等谁；② artifact 仍是 11 件 ⇒ 结束它的只有
  GitHub 那一刀，问题在 runner 侧，不再往步骤里加仪器。这条**顺序关系**被
  `test_the_suite_step_keeps_the_probe_that_makes_a_death_findable` 钉住：步骤预算必须小于 job 上限的
  60 倍，且判据读的是解析后 `env` 的**值**而不是 `str(dict)` 的打印形状——第一版用 `re.search` 去匹配
  双引号字面量，`str(dict)` 打出来是单引号因而匹配不到，判据当场具名红了一次（它是响的，不是静默跳过）。
  变异电池加到 29 条：`M-Y` 把预算抬回 1200、`M-Z` 把上限抬回 90、`M-M` 干脆删掉上限，各自点名变红。
  本轮只动工作流与判据，**不涉及发布包内容**：版本号仍停在 2.11.1，独立仓与 tag 不新增。

- **挂住的那一步不能从步骤里结束，所以把上限放到 pytest 自己身上（`pytest-timeout`）。**
  三条 run 把这件事钉死了：`timeout --kill-after=60 1200`、`setsid` 完全脱离 + 有界轮询 +
  `kill -9 -- -$pid`、以及 `gh run cancel`——**三种形状都没能让 bucket 06 那一步结束**，步骤状态一路
  `in_progress`，最后只有 GitHub 自己的 job 上限动刀（实测 30 分钟上限 ⇒ 起于 13:58:00、 cut 于
  14:33:00＝整 35 分钟＝上限 + 约 5 分钟宽限），而那样的切断**不上传任何 artifact、不留任何日志**
  （`artifacts.total_count` 恒为 11＝十条绿腿 + tarball）。结论：步骤内的上限只能证明"步骤该结束了"，
  不能让它真的结束；能命名的只有 pytest 自己还活着时使用的那把表。
  新增 dev 依赖 `pytest-timeout==2.4.0`，`[tool.pytest.ini_options] timeout = 240 /
  timeout_method = "signal"`。判据不是拍脑袋：本机 `--durations=12` 实测最慢单条 10.90 s
  （`tests/test_ci_suite_step_program.py::test_the_control_step_…`）、整套 3,119 条 124.93 s ⇒ 240 s 是
  最慢用例的约 22 倍。控制实测：对一条故意 `sleep(300)` 的用例，`-o timeout=5` 下它按名字红
  （`Failed: Timeout (>5.0s) from pytest-timeout`）、隔壁用例照旧过、会话 5.04 s 返回。
  三道上限现在的次序写进判据：**一条用例 < 一步 < 一个 job**
  （`test_a_hanging_test_is_bounded_by_pytest_itself_not_by_the_step`），任何一道被抬到上一层之上就红。
  判据本身先红过一次才修对：我第一版用 `split("[", 1)` 切 `[tool.pytest.ini_options]`，那正好切在
  `testpaths = ["tests"]` 上，读到空段 ⇒ "没有 per-test timeout"；改成按行首 `[` 切段。同样按实测改掉的
  还有我自己那条量法：找 `extra == "dev"` 用了双引号，而 hatchling 写的是单引号——**"wheel 里没有 dev
  依赖"这句是我量错了**，实测 METADATA 里有 7 条 extra 门控的 `Requires-Dist`，其中一条就是
  `pytest-timeout==2.4.0; extra == 'dev'`。
  **这条量错反过来定了一个发布判断**：dev 依赖进 METADATA ⇒ 本笔改动**动了发布物**，
  而 CI/`.github`/`scripts`/`tests` 那些路径实测不进 wheel（`unzip -l` 数到 0 条）⇒ 前几笔不需要号，
  这一笔需要。下一次取号 **2.11.2**（patch：发布物字节变了而语义没变）。
- **上限放对了层：第四条表（进程看门狗）才是看得境 teardown/退出那一段的那把。**
  run 40207198620 交出第四次同读数：预算 600 s、上限 20 分钟的一次运行里，bucket 06 两条腿仍
  `in_progress`，最后仍由 GitHub 的 job 超时在 15:52 前后切断（artifact 恒 11 件）。把四次放一起看：
  `timeout --kill-after` 不能结束步骤、`setsid` 脱离 + 有界轮询不能结束步骤、`gh run cancel` 不能、
  `pytest-timeout` 的 240 s 逐条表也**从没响过**——最后这条是新信息：逐条表覆盖 setup/call/teardown，
  它不响就说明等待**不在任何一条用例里**，而在最后一条之后的那段（session teardown 或解释器退出，
  那里可能挂着非守护线程或自己不肯死的孩子）。
  因此加上 stdlib 里唯一看得见那段的那把表：`faulthandler.dump_traceback_later(N, exit=True)`，
  在 `tests/conftest.py:pytest_configure` 里按 `ITERATE_TEST_WATCHDOG` 上弦（CI 设 420 s，本机不设
  ⇒ 不弦，因为本机整套在负载下量到过 36m27s，一条 7 分钟的进程级看门狗会杀掉一次合法的慢运行）。
  四道上限现在是嵌套的，并由判据钉住次序：**一条用例 240 s < 进程看门狗 420 s < 步骤预算 600 s
  < job 上限 20 分钟**。
  两次自己踩到的坑都写进判据里而不是删掉：**（一）**头两版把 dump 打到 `sys.stderr` 与
  `sys.__stderr__`，结果整个运行**空输出**——pytest 默认 `capture=fd`，它把 fd 1/2 本身重定向进自己的
  临时文件，而 `exit=True` 杀进程时那份捕获缓冲根本来不及落盘。修法是自己 `open()` 一个句柄
  （`ITERATE_TEST_WATCHDOG_FILE`，CI 里是 `pytest-watchdog.log`）并把模块级变量钉住不让它被关；
  artifact 现在同时上传两份日志。**（二）**第一版控制用例的断言写的是 `line 4`，那是独立实验里
  `time.sleep` 的行号；在这个夹具里 4 行是 `def`、5 行才是阻塞处——改成要求帧里出现
  `in test_sleeps_forever`，而不是记一个会从别的文件漂过来的数字。
  控制实测（本机，`tests/test_test_watchdog.py`）：`ITERATE_TEST_WATCHDOG=4` 下一条 `sleep(120)` 的
  用例，看门狗文件里是 `Timeout (0:00:04)!` 加上 `File "…test_watchdog_probe.py", line 5 in
  test_sleeps_forever`，子进程 **退出码非 0**（独立量过一次：`rc=1`，看门狗杀掉是一次红，
  不会伪绿）。同时两条拒绝分支也实测能红：变量缺失 ⇒ 不上弦（正常运行 rc=0、无 armed 字样），
  `not-a-number`／`-5`／`0` ⇒ 具名拒绝并退出。
  本机整树：`3120 passed, 6 skipped in 127.16s`；ruff 与 mypy 绿；判据 37 条全过。
- **挂起那一格剩下的唯一出路：一条不需要步骤结束就能离开机器的通道（flight recorder）。**
  六把表都不响之后，事实是：日志、annotation、artifact 这三条通道全都要等步骤先结束，而这条
  等待本身不会发生。所以 `Run test suite` 里加了一个记录器——每 60 秒把 `pytest-ci.log` 与
  `pytest-watchdog.log` 的当前内容推到 `refs/heads/diagnostics/<run-id>-<版本>-<桶>`，
  job 因此需要 `permissions: contents: write`，而这条权限被逐条判据钉在自己的用途上：
  ① 只推那一个具名前缀（`REC_BRANCH` 由 run id + matrix 决定，桶之间不撞名，也永远够不到
  `refs/heads/main`；仓里的 ruleset 另外单独保护 main）；② 工作目录在 `RUNNER_TEMP`，
  **绝不在 checkout 里**——本套件有好几条用例是拿 `git status` 判事的，把记录器写进树里就是让它
  改变被测量的东西；③ 每个 git 调用都 `|| true`（"仪器不能变成故障"这条上一笔刚量过）；
  ④ 没有凭据就**原地失效**并打印 `recorder started: enabled=$REC_PUSH`——第一版没这条门，
  `set -u` 下裸 `$GITHUB_RUN_ID` 直接被实测成 `unbound variable`，把整个步骤打死在启动之前；
  ⑤ 用例桶正常跑完时删掉那个 ref（那时 artifact 才是权威副本），只有预算耗尽的腿把 ref 留下——
  也就是"除了这条路没有别的证据"的那种腿。
  本机两处实测把上面两条钉成事实而非愿望：`tests/test_ci_suite_step_program.py` 现在**显式清掉**
  子进程环境里的 `GITHUB_TOKEN/GITHUB_REPOSITORY/GITHUB_RUN_ID` 并断言 `enabled=0`
  （否则谁导出过一个 token，单元测试就会往真仓库推真分支）；`tests/test_ci_workflow_shape.py` 新增
  两条判据管通道的形状与"记录器不许启动/包装/判断套件"。电池里跟着加四发：
  `N-1` 拆掉凭据门、`N-2` 把记录器搬进 checkout、`N-3` 摘掉 push 的 `|| true`、
  `N-4` 让它不再自报开关状态，各自点名变红；电池共 33 条，全 RED-AS-SPECIFIED。
  判据一处也修窄了：原先"带 `>/dev/null 2>&1` 就算安全"的说法是错的——重定向只是把消息藏起来，
  失败仍然失败。现在要求 `|| true`（或续行）。
  README（双语）同步补上"CI 不再把整套当一个进程跑"、四道上限的嵌套次序、`ITERATE_TEST_WATCHDOG`
  这颗可调的表，以及**按实测写明的已知问题**：`bucket 06` 那 31 个模块在 ubuntu 上让 runner
  结束不了那一步，本机因缺 docker/node 不可复现。

- **flight recorder 一上线就改写了这一格的诊断：挂的不是用例，是"收集"。**
  run 40244447182 的 `refs/heads/diagnostics/40244447182-3.11-06` 只有一个 commit（`rec 23:23:13`），
  里面那份 `pytest-ci.log` 605 字节，最后一行是 `collecting ...`——之后 11 分钟一个字节都没再写。
  **本会话此前写的"等待在最后一条用例之后（teardown／解释器退出）"这句被它自己的产物推翻**，
  收回并改成实测：收集期。这一条同时解释了为什么六把表一把都不响：`pytest-timeout` 与
  `faulthandler_timeout` 都是**逐条**的表，收集阶段根本没有条目可计时。
  而我那把 `faulthandler.dump_traceback_later(420, exit=True)` 为什么也不响：faulthandler 在一个进程里
  只有**一个** "later dump" 槽位，pytest 自己在每个条目前后 arm/cancel 的就是这个槽位——最相容的解释是
  我的表被它悄悄取消了。**注意措辞的边界**：量到的是"420 s 到点没有任何输出"，
  "被 pytest 取消"是与它最相容的机制，不是被直接观察到的因果。
- 于是表搬家：`tests/conftest.py` 改用一个**守护线程**睡到点后 `dump_traceback(all_threads=True)` +
  `os._exit(1)`，不占 SIGALRM、不与他人共享槽位，也因此第一次真正覆盖收集阶段。控制用例同步改
  （`Thread 0x…` 线程表 + 帧里出现 `in test_sleeps_forever` + 退出码非 0；本机 5.9 s 内杀掉）。
- 步骤里再加两件让"没有新证据"本身成为证据的东西：**轮询心跳**（每个 tick 由 bash 写
  `$RUNNER_TEMP/gp-poll.heartbeat`，recorder 附带推送）——它是"bash 还在轮询"与"bash 早已退出而
  runner 没报"之间唯一缺的判别；以及 recorder 的每个 git 调用都 `timeout` 包一层并置
  `GIT_TERMINAL_PROMPT=0`，否则一次卡在代理上的 push 会让"ref 没有新 commit"什么都不能说明。
- 判据自身也修了两处假绿，都来自同一种病：**按物理行断言**。记录器的 push 跨三行，"这一行有反斜杠"
  永远为真，于是把 `|| true` 摘掉的变异 `N-3` 照样绿——现在把续行拼成语句再判（`_push_statements`），
  并要求每个 push 有 `|| ` 兜底（`|| echo` 与 `|| true` 等价，删掉才红）。另一处：看门狗机制搬家之后，
  断言找的 `dump_traceback_later` / `exit=True` 仍然在文件里——留在解释"为什么不再用它"的散文里；
  现在用 `_call_lines` + `_is_call_site` 只认**行首是调用**的行。变异 `W-1` 把表换回 SIGALRM 计时器，
  点名红。电池共 34 条，全部具名变红；本机整树与四条判据文件复跑见下条提交信息。
### Documentation

- **「🔬 GlassPane integration / 与 GlassPane 的集成」四句按代码改写。** 全新眼界的复审读的是代码而不是
  这几句话，抓出四处与实现不符：① 原文写"每次 `gp_*` 调用都回一份证据包"——引擎里
  `["evidencePack":` 的出口全仓只有一处（`Dispatcher.swift:216`，`handleLastEvidence`），其余四项工具
  不回包；② 原文写"两侧都能整链验通对方那本"——生产里跑这个读者的是回路这一侧（`fix_proof`），fork
  带同一个校验器、也有固定点用例证明这边写出的链在那边验得过，但它自己的生产路径只写不读；③ 原文把
  允许清单（只读五项）说成回路会问的调用，而它真正构造的是每个操作一条 `gp_last_evidence` 加一条
  `gp_diagnose`；④ 原文"计划显式交接走 `GLASSPANE_RUN_PLAN`"容易被读成本项目会设这个变量——本项目写的
  是 `.iterate/run-plan.json`，那个环境变量是操作者那一侧的交接。闸那一句跟着上面第一条修复一起改
  （通过轮也落账、契约拒绝不落账）。中英两版逐条同步。
- **父仓 README（双语）里那句"各自整链验通对方那本"同样收成实测形状**：读者在哪一侧、另一侧靠什么
  证明，分开写清。

- **④ 的闸答多了一条操作者验收：`scripts/check_gate_answer_reader.py`。** 它验的不是形状而是
  "装好的那份读得回来"：跑一轮真通过的项目（`spawned=False` + `reason='the invariant report passed…'`），
  要求台账里恰好一行 `kind: recipe-gate`，再用 subprocess 问发布出去的 `ih iterate gate` 把答案打出来。
  三条退出码分得很清：0 两头都通；1 落账了却读不回来，**或那行长出了 `operationId`/`outcome`**——那会让
  闸自己开自己（下一轮把回路自己的答复当成"修复发生过"的证据）；2 什么都没量到（PATH 上没有 `ih`，
  或给的路径执行不了）。"问不到"与"答案不对"必须分码，否则一台没装 CLI 的机器会被读成一次失败的门禁。
  反向变异当场验过它能红：把两处 `if _answered_by_passing_round(outcome):` 改成 `if False and …` 后，
  它按名字报 `expected exactly one recipe-gate row for one passing round, found 0`；变异用备份 +
  sha256 核回（`d931658b…`），不走 `git checkout`。它是操作者命令、不进 CI——与另外三条
  `check_glasspane_*` / `check_run_plan_channel` 同一待遇，理由也相同：需要对面在场或需要装好的 CLI 的
  检查，写成会 skip 的测试就是又一次"没人跑的绿灯"。

- **我自己上一批把 CHANGELOG 里一个已发布版本的整段正文删掉了，修复这一笔只插入、不重组。**
  事故在 `3676343e`：本意是改一条 bullet 的措辞，diff 却是 +19/-50，被切走的一刀连带删掉
  `## [2.11.1] - 2026-10-25` 这个标题、它的版本判断段与 7 条 bullet，以及 [Unreleased] 里记
  `scripts/check_gate_answer_reader.py` 的那条。也就是说 2.11.1 早已发出去了（tag / Release / PyPI / npm
  四格齐），而它的台账在这份文件里一度不存在。本仓对这类错有写过的教训（合并解析时不许拿
  head+theirs+mine 重组文件），当时的防线就是"改完数一遍 section 与 bullet 条数，跟父提交比"——
  这次没执行那条防线，所以它是来第二次。修复不搬动任何文字：从父提交 `3676343e^` 按字节取出那段区域
  （91 行 / 8 条 bullet / 10,141 字节），插回它原来的位置（当前 `## [2.11.0] - 2026-10-25` 之前），
  事后四条断言各跑一遍：插入文本与父提交逐字节相等、每个版本标题恰好一次、恢复后的 [2.11.1]
  bullet 数 = 7、`git diff --numstat` 删除列 = 0。
  同一批还留下第二个自己的错，形状小但同类：那条修复的提交信息（`e2bfef57`）是用未加引号的
  `printf` 写的，于是我写进信息里的反引号被 shell 当成命令替换执行了一遍——`3676343e`、
  `scripts/check_gate_answer_reader.py`、`record_gate_without_send`、`3676343e^` 四个名字从信息里被吃掉
  （四条命令都是 command not found，没有任何副作用，但它们也不该被运行）。内容无损、全过程在本条里，
  不改写已提交的历史；从这里起所有提交信息一律写进文件再 `git commit -F`，不在命令行里拼。

## [2.11.1] - 2026-10-25

**版本判断：patch。** 这一版没有新增任何用户可见能力，它做的是一件更早的事：2.11.0 的 README 与注册表描述宣称「判定闸的答案随那一轮的账本行落盘，两扇门都读得回来」，而落账的只有「发出去了」那一支——通过轮把答案留在瞬时 payload 里，读台账的两扇门永远读不到。这一版把那条宣称变成事实 (`record_gate_without_send`)，改掉四句与代码不符的文档，并补两把此前不存在的闸（懒加载表可达性、README 清单与 handler 一致）。既有形状一个都没动：`requires-python >= 3.10` 未变、CLI 参数与退出码集合未变、内核仍是 `iterate-kernel@0.1.10`、`.iterate/decision-log.jsonl` 的行格式未变（新增的是一行 `kind: recipe-gate` 的记录，而 `fix_proof` 不会把它当成修复证据——它缺 `fix_proof` 要的两样）。所以不是 minor（没有新能力），也不是 major（没有破坏）。

- **判定闸的答案在"通过所以什么都没发"的那一轮也会落账了——在此之前两扇门永远读不到它。**
  `_prepare` 每条分支都算 ④ 的闸（通过的、契约拒绝的、发出去的），但落账的只有"发出去"那一条，
  而 `/iterate gate` 与 `ih iterate gate` 读的是**台账**。于是一个项目跑十轮通过的轮次，终端门
  仍然回答 "no recipe gate recorded yet"——本轮就实测到了这句：那条通过轮在 `iterate_state` 里
  明明发布了 `invariantState=pass` 的闸答，账本里却一个字节都没有。瞬时的 payload 不是读者，它随
  轮次一起蒸发，而"发布了一个没有读者能达到的状态"正是本仓反复被罚的形状。
  新增 `record_gate_without_send`：只给**做成过决定**的那一条（判定通过、因此什么都没发）落一行
  （`recipe-gate`），契约拒绝仍一个字都不写——Requirement C 钉着那条路径（
  `test_a_refusal_path_writes_zero_ledger_bytes`）：一个从未做成的决定不该变成一条审计记录。这行也不是
  修复证据：它既没有 passing outcome 也没有 operationId，而 `fix_proof` 要的就是这两样，`
  test_a_gate_row_cannot_be_read_as_the_fix_it_is_talking_about` 把形状本身钉住（给它加 operationId
  就红）。台账写不进也不取消这一轮：错误文本进 payload 的 `ledgerError`，与发出的那条同一个契约。
  变异电池 3 组 7 条具名红（撤掉落账 / 懒加载表塞死名 / 给这行加 operationId），逐文件 sha256 核回。

- **懒加载导出表里有两个名字指向不存在的符号，套件全绿。** `iterate/__init__.py` 的 `_EXPORTS` 把
  `MAX_REQUESTED_RECIPE_STEPS` 交给 `.reverify_trigger`，而那个常量早已改名成 `MAX_REQUESTED_OPERATIONS`；
  `recipe_steps_not_requested` 则在本轮 ④ 的闸挪位时被删了实现，表和 `__all__` 都没跟。两者实测都是
  `AttributeError: module 'iterate_harness.iterate.reverify_trigger' has no attribute …`——**数静态
  import 抓不到这种名字**，字符串加载器只在有人真去取的时候才炸。新增
  `test_every_name_the_lazy_export_map_advertises_actually_resolves`：逐条 import 目标模块并要求符号存在，
  再要求 `__all__` 里每个名字都答得出。这两条死名由此被抓出并归位。

- **`/iterate` 不带参数打印的是这个项目当前状态，不是子命令清单。** 上一轮我为了"抄来的清单每加一条
  子命令就漂一次"把 README 与注册表描述改成"不带参数会打印当前清单"——这句本身也是一句抄来的话，
  实测是错的：裸命令走 `status` 分支（配置摘要 + 决策日志条数 + 引导提示），**打印清单的是不认识的
  子命令**。现在两处 README、`registry.py` 的命令描述都按实测改写，并新增
  `tests/test_iterate/test_iterate_slash_surface.py`：把 README 里那份 16 项清单与 handler 的分支
  条件逐字比相等（两个语言各一条），两个入口各跑一次真 handler 判形状。控制实测：把注册表描述改回
  旧句、把中文清单里的一项改名、把裸命令改派到未知分支，三种都各自具名红。

- **跨项目验收 `scripts/check_run_plan_channel.py` 修好了一件它此前一直在隐瞒的事：默认调用
  从来没有测量过任何东西。** 两个默认值各多了一段目录——
  `REPO.parents[1] / "iterate-skill" / "config" / "iterate.config.yaml"` 实测解析成
  `/Volumes/Eng-Dev/iterate-skill/iterate-skill/config/iterate.config.yaml`，
  `…/iterate-skill/GlassPane/harness/glasspane-harness` 同理，两者的 `exists()` 都是 `False`——
  所以不带 `--config/--harness` 的默认运行在写出计划之前就 `return 2`。退出 2 的意思是
  "没有测量发生"，不是"测量通过"，而它被当成后者引用过。现在按仓库真实布局逐候选发现路径，
  发现不到就具名拒绝并退出 2。
  同时补上本版才交付的第二条通道：`.iterate/run-plan.json` 项目文件通道（`readRunPlan({}, dir)`
  而不是只喂 `GLASSPANE_RUN_PLAN`），并断言回包的 `plan.source` 等于所测的那条通道
  （`env` / `project`）——否则两条通道可以实际读同一份环境变量，却都被记成"通过"。
  实测读数：9 个维度、digest
  `rp_68049dadeff6c85c4807448b04f44462c24f069b8936e57b181a667b8f42380e`、两条通道各自
  `channel agrees`、退出 0。

- **主干 CI 的测试套件那一格：四次同死法，两版仪表，一个当场量到的时长。** 死法是先跑起来再无输出，
  job 在起跑后第 45 分钟整被 "The hosted runner lost communication with the server" 打掉，而 runner 一死
  日志就不上传（`--log-failed` 报 `log not found`，`runs/{id}/logs` 的归档里只有三条成功的 job）——
  run 40167475388、40168346235、40103418285（v2.10.0）与 40171467085。**第一版仪表不够**：只加了
  pytest 自带的 `faulthandler`（`-o faulthandler_timeout=300 -o faulthandler_exit_on_timeout=true`，
  零新依赖；对本机一条故意 `sleep(60)` 的用例实测 rc=1 并报出
  `File "…/test_hang_probe.py", line 3 in test_hangs`），而带上它的 run 40171467085 仍以同一姿势死在
  同一分钟，日志照旧没有。于是补第二版，并同时记下为什么不能断言"这是挂起"：本机跑同一套是
  **36m27s / 3083 条**——45 分钟的沉默同样可以是"这套本来就比 runner 的沉默窗口慢"，不是证据指向挂起。
  第二版做三件事，两件同时服务于两种成因：① 独立一步 `pytest --collect-only -q`（收集期就卡住＝某个
  模块在 import 时阻塞，而那正是 `faulthandler_timeout` 看不见的地方）；② 跑测试改用 `-v`（逐条打一行，
  日志通道不再沉默——慢的套会跑完并报告，真挂的会留下最后那条名字）；③ job 级 `timeout-minutes: 90`
  （有上限才会**带着日志结束**，而不是让 runner 把证据带走）。90 不是猜的，是按上面那个实测留的余量。
  判据写在步骤注释里：红了按 dump 或最后一条 `-v` 找出等的是谁；不许抬 `faulthandler_timeout` 让红消失、
  不许改成 skip、更不许为了"日志太长"把 `-v` 拿掉——那行逐条输出正是这一步可寻的原因。

### Documentation

- **「🔬 GlassPane integration / 与 GlassPane 的集成」四句按代码改写。** 全新眼界的复审读的是代码而不是
  这几句话，抓出四处与实现不符：① 原文写"每次 `gp_*` 调用都回一份证据包"——引擎里
  `["evidencePack":` 的出口全仓只有一处（`Dispatcher.swift:216`，`handleLastEvidence`），其余四项工具
  不回包；② 原文写"两侧都能整链验通对方那本"——生产里跑这个读者的是回路这一侧（`fix_proof`），fork
  带同一个校验器、也有固定点用例证明这边写出的链在那边验得过，但它自己的生产路径只写不读；③ 原文把
  允许清单（只读五项）说成回路会问的调用，而它真正构造的是每个操作一条 `gp_last_evidence` 加一条
  `gp_diagnose`；④ 原文"计划显式交接走 `GLASSPANE_RUN_PLAN`"容易被读成本项目会设这个变量——本项目写的
  是 `.iterate/run-plan.json`，那个环境变量是操作者那一侧的交接。闸那一句跟着上面第一条修复一起改
  （通过轮也落账、契约拒绝不落账）。中英两版逐条同步。
- **父仓 README（双语）里那句"各自整链验通对方那本"同样收成实测形状**：读者在哪一侧、另一侧靠什么
  证明，分开写清。

## [2.11.0] - 2026-10-25

**版本判断：minor。** 判据是加法语义那一条：本版**只新增用户可见能力、不动任何已有形状**——
④ 第一次可执行（配方交接 + 判定闸 + 闸随它那一轮的账本行落盘）、③ 的跨账本链接（对面那本可被
项目记为默认）、两条读门（`/iterate gate` 与 `ih iterate gate`、`ih iterate glasspane-ledger`）、
`requires-python >= 3.10` 未动、CLI 既有参数与退出码集合未动、内核仍是 `iterate-kernel@0.1.10`
（语料清单的 pin 从 0.1.9 追到 0.1.10，10 份文件逐字节实测一致，两模式各跑一遍）。所以不是
patch（有新能力），也不是 major（没有破坏任何东西）。

### Documentation

- **README 双语新增「🔬 GlassPane integration / 与 GlassPane 的集成」一节**（目录各补一条锚点）：
  四条能力各自写清"谁生产、谁消费、拒绝时长什么样"——证据链与跨账本链接、维度声明与见证互斥、
  只读重验的工具白名单、修复配方的三件实测与两条读门——并把边界写在同一段里：应用做了什么由
  GlassPane 判，那一轮算不算已验证由本 harness 判，两边都没有配方执行器。
  顺带清掉两处过期枚举：`/iterate` 的子命令清单在两处被写成六条（实际 16 条），改成"不带参数执行
  `/iterate` 会打印当前清单"——抄来的清单每加一条子命令就漂一次。
  主仓库 README（双语）里那句"GlassPane 是相关项目、刻意独立"下面补了这一段：独立不等于不往来，
  并指到本 README 那一节。

### Added — 场景④ 第一次可执行：配方交接、判定闸、跨账本佐证

综述 §8.5 row 4（"GlassPane 修复完成 → 配方下一步挂接 iterate invariant 复核，通过后才进入重验"）
此前在本仓**没有任何落点**：`reverify_trigger` 拒绝产出 `recipeSteps`，理由写在它自己的 docstring
里——"a step is something the engine's own recipe carries, and this harness has never held one"。
本批不新增内核 API、不发明配方执行器，只把现有契约能承担的部分落成可执行、可拒、可红的东西。

- **`iterate/glasspane_recipe.py`（新）**：配方从两个真实生产者取得——`GLASSPANE_RECIPE`
  （与 `GLASSPANE_RUN_PLAN` 同一套交接纪律）或 `gp_project_list` 报出的 `recipeConfigPath`。
  文档按 `glasspane.recipe/0.1-draft` 校验，这是该契约的第二份实现，语料钉在
  `tests/kernel_fixtures/recipe-config.ok-01.json`（sha256 `68613bca…`，`check_kernel_fixtures.py`
  两模式实测 10 个文件逐字节一致）；摘要取 `rc_` + canonical sha256，是本地出处凭据而非契约字段
  （内核没有配方摘要）。两个生产者各说一套 → `RECIPE_E_CONTEST`，不挑一个顺眼的用；registry 回包
  认不出形状 → `RECIPE_E_READ`，**不降级成"没有配方"**；多项目各配一份 → 拒绝，因为"这次评审属于
  哪个项目"不是本模块能猜的。
- **判定闸 `recipe_step_gate`**：按固定次序读三件实测——本轮 invariant 判定、配方、一条**链可验**的
  账本行。`unmeasured` 与缺席判定都不算 pass；缺任何一件都具名拒绝（六个码）。
- **③ 的账本第一次有对面读者**：`fix_proof` 走内核链校验器（`read_decision_log`），既能读工程自己的
  `.iterate/decision-log.jsonl`，也能读 `GLASSPANE_DECISION_LOG` 递进来的**另一个 harness 写的**账本。
  断链拒绝并说停在第几行；pre-chain 的旧形状另给一句"这是历史不是篡改"。
- **`recipeSteps` 不进出站请求**，这条是本批第二版才定下来的。第一版让①的失败请求带上配方步骤，
  读起来像"④  closed"，实际违反 ④ 自己要的那件事：复核没通过时不许让对面重跑步骤。而契约在这一层
  没有缺口可钻——`reVerificationRequestFrom` 两份实现对 pass 一律返回 null（失败才生成请求），所以
  "带权限的请求"按构造不存在。于是本模块把这条写成**禁令**而不是能力：`reverification_targets` 没有
  收配方的参数，`test_targets_never_carry_recipeSteps_however_the_recipe_looks` 同时钉住返回值与
  函数签名（参数回来就得红）。配方本身仍被读、被校验、被摘要——它以 `recipeStepGate.steps` 出现在
  本轮发布里，那是给驱动配方的人看的事实，不是本仓替引擎动手的许可。
- **发布点接线**：`recipeStepGate` 在**每一条**能发布的路径上一起出去（通过、契约拒绝、以及真正
  发出的那一轮），不再只挂在 pass 分支——只挂一边的话，`RECIPE_GATE_E_*` 那两条拒绝按构造跑不到，
  而"跑不到的拒绝分支"在本仓等于没修。sync 与 awaited 两个 sender 仍共用 `_prepare`；新增的唯一一次
  通道使用是那一次 registry 读，它被 `CONFIG_READ_TOOLS` 与 `READ_ONLY_TOOLS` 分开钉住——**读配置
  不许冒充一次关于 app 的回答**，`test_reverify_wiring` / `test_host_transport` 各有一条断言把两个
  集合分开数。

### 实测与反向验证

`pytest tests` 全量 2966 passed；6 条红（`test_tasks/test_manager.py` 三条、
`test_tools/test_bash_tool.py` 一条、`test_tools/test_integration_flows.py` 一条、
`test_engine/test_query_engine.py::test_subagent_stop_hook_fires_when_spawned_agent_finishes`）
在 `d14da4b2` 的**干净基线工作树**上同样红，属本机环境而非本批引入；
`ruff check src tests scripts` 干净、`mypy --strict src` 267 文件干净。
新增 62 条用例（`tests/test_iterate/test_glasspane_recipe.py`）。十发反向变异
（`/var/tmp/it-mutate.sh`，备份 + sha256 核回，全程未用 `git checkout`/`stash`）每发都点名变红：
unmeasured 当 pass、放行无可验账本、contest 悄悄取一个、多配方取第一个、回包降级成"没有配方"、
断链改用宽容读法、判定顺序颠倒、发出去的那轮不带闸、未通过也塞 recipeStep、放行面放宽到 `gp_act`。
闸的六条码里，`RECIPE_GATE_E_NO_VERDICT` 在本轮**发布不到**：无判定的轮次按规则 1 连 `reverification`
都不发布（`_prepare` 第五个位置此时返回 `None`，不为它编一个没人读的形状），所以这条是给直接调用方的
契约、由测试钉住形状，不算"产品上会看到的拒绝"。

### Added — ③ 的最后一程（把对面那本账链接进来）与 ④ 的读者

上一条留下的两处"能做但没人配置"，本批各自接到底：

- **`.iterate/glasspane-ledger.json`（`iterate.glasspane-ledger-link/0.1`）**：`fix_proof` 早就能读
  对面写的链，但"去哪儿读"只剩 `GLASSPANE_DECISION_LOG` 一条环境变量——没配的那台机器上，③ 静默退化成
  "只读工程自己的台账"，而 GlassPane 修的东西恰恰不在那本里。现在 `link_ledger` 把交接做成一次性的
  项目动作：先跑内核链校验器**再落笔**（校验不过就一个字节都不写，因为给坏链背书会让之后每一轮都拒绝，
  而操作者是在最坏的时机——轮次中间——才知道），钉住 `genesisEntryId`，0700 目录里 0600 文件。空台账允许
  链接，但 genesis 留空＝弱一档的背书，这话写在链接时而不是等读的时候才发现。
- **解析次序 `env` → `link` → `project`**：显式交接仍然压过记录，操作者必须能在不改文件的前提下临时换一本。
  genesis 与文件首条不符 ⇒ `LEDGER_LINK_E_STALE`：一条链只会变长，首条换了就是被**替换**了，从替换品里
  引一条"修复已证"等于引用一条没人见证过的链。截断则先被链校验器抓住（第 1 行）——两条拒绝指向不同地方，
  所以次序是量出来的而不是随手写的。
- **④ 的闸有了不是测试的读者**：`recipeStepGate` 现在随它那一轮的账本行一起落盘，`/iterate gate`
  把"上一轮答了什么"读回来。读法故意与 `fix_proof` 不同——用宽容投影器，因为它答的是"这一轮发布了什么"，
  这个问号在别处断链时也必须还能答。没有记录时它说"那是缺席，不是通过"，不打印一个绿色的默认值。
- **`/iterate glasspane-ledger [<路径>]`**：带路径就链接（把对面打印的绝对路径贴进来），不带就打印当前
  是哪一本、链的是谁、链不链得通。两条子命令都进了 usage 文本——发现不了等于没有。

### Fixed — 三条自己写出来的、形状已知的缺陷（复核时抓到，不是别人报的）

- `LINK_E_SCHEMA` **声明了却没人举**，而 `read_ledger_link` 把手写过/半写的链接当"没有链接"，悄悄退回
  工程自己的台账。那不是"降级"，是给错的文件作背书：操作者以为读的是对面那本，代码读的是另一本。
  现在只有"文件不存在"才回 `None`，不合形状/不是 JSON/读不到各自具名拒绝。
- `os.open` 在 `try` **外面**：链接路径被一个目录占住时抛裸 `IsADirectoryError`，而闸的调用方只处理
  `RecipeError`。两处 OSError 出口现在都包进同一条 `LEDGER_LINK_E_READ`。
- 两条从未被 raise 的码（`LEDGER_LINK_E_STATE_ROOT`、`LEDGER_LINK_E_LEDGER_EMPTY`）删掉——本仓的规矩是
  声明一个不存在的拒绝＝给不存在的能力作背书。
- **`from datetime import UTC` 是 3.11 的**，我把它写进了 `commands/iterate.py`，`tests/test_py310_compat.py`
  当场红（本包 `requires-python >= 3.10`）。改成仓内既有的 `from datetime import datetime, timezone`
  形式；下一笔又把时间戳整个搬进 `glasspane_recipe.link_ledger_now`，所以 `commands/` 里现在**根本不出现**
  时间戳格式（有一条用例数着全仓 `isoformat(timespec="milliseconds")` 只出现 1 次）。
  顺着这条把闸本身也修了：它此前只扫 `src/`，而一个测试文件里的 3.11-only 名字在 3.10 上是**收集期**
  ImportError——整文件用例静默消失，本机 3.13 上永远看不见。**这条仓的 CI 分布是量出来的，不是想象的**：
  父仓 `iterate-skill` 的 push CI（`Python tests (3.10…3.13)` 那份矩阵）跑的是根上的 `pytest tests/ -q`
  ——实测 1865 passed，那是 **skill 自己的套件**，碰不到 `harness/iterate-harness/tests/`；harness 套件
  的 3.10 覆盖在 `harness/iterate-harness/.github/workflows/ci.yml`（`uv run pytest -q`），而那份工作流
  只有 **subtree push 到 `jingzhao-l/iterate-harness` 之后**才作为该仓根工作流执行。也就是说：一个坏掉的
  测试导入在本仓 push 上是**静默**的，要等搬运之后才红。静态闸把 `tests/` 纳进扫描面之后，任何一次跑
  harness 套件（本机也算）都能看见它——这才是这条修法的价值所在。
  同时给"整模块导入"补了 guarded-import 豁免（`try: import tomllib / except ModuleNotFoundError:
  import tomli as tomllib` 是本仓已有写法，误报会逼人删掉兜底换绿）；豁免只到整模块导入为止，
  `from datetime import UTC` 那种"名字"包在 try 里不豁免——那正是 2.5.0 事故的形状：3.10 的失败落在
  `try` 内、被 handler 咽掉，代码安静降级。

### Fixed — 语料清单的 `version` 从 0.1.9 追到 0.1.10（并两次实测它是同一份字节）

`tests/kernel_fixtures/manifest.json` 的 pin 停在 `iterate-kernel@0.1.9`，而对面那条线（harness 的
`contracts/kernel-pin.json`）早已吃 0.1.10——"两仓吃同一份内核"这句话此前靠一个说 0.1.9 的标签撑着。
先证明字节没漂，再动标签：`scripts/check_kernel_fixtures.py --source <0.1.10 安装产物的 fixtures/>` 与
registry 模式（`npm pack` 真包）**各跑一次，10 份文件逐字节对上**。0.1.10 那版内核动的只有 `zod`，
契约语料未变，所以这是一次追标签而不是迁移。

### Added — 那两个读者要有第二扇门：`ih iterate gate` / `ih iterate glasspane-ledger`

上一条把读者写成了"只有 session 里 `/iterate …` 敲得动"。拿真 `ih` 一试，终端回的是
`No such command 'gate'`——Typer 那面给 `status`/`log`/`trend`/`agents` 都配了孪生命令，我新加的这两条
没有。一个只能从代理里问的读者，仍然是一个读者，但操作者坐在终端前问不到，就等于他手上没有这个数。

- 两条 Typer 命令落进 `cli.py`，与 slash 面共用同一批函数；**退出码按面分**（终端拒绝退 1，
  session 里把拒绝当答案返回），**措辞按面统一**（同一个 `format_recipe_error` / `render_link_success`）。
- `linkedAt` 的格式从 `commands/` 搬进 `glasspane_recipe.link_ledger_now`：上一条刚把那里的
  `datetime.UTC` 修成 `timezone.utc`，而真正的病是"每个调用点各排一次时间戳"——两个面各排一次，
  就是两份会各自漂的格式。现在 `isoformat(timespec="milliseconds")` 在全仓出现 1 次，由一条用例数着。
- registry 里 `/iterate` 那条描述此前抄了一份六条的命令清单（实际 14 条，`gate` 与
  `glasspane-ledger` 都不在内）。改成指向 usage 文本："不带参数就打印当前清单"——这句抄不旧。
- 两面一致性由三条用例钉住（同一条项目、同一个 cwd，终端输出与 slash 输出逐字相等，只豁免
  `linkedAt` 那个墙钟字段），拒绝那条另断言 `exit_code == 1` 且**没有留下链接文件**。

### 实测与反向验证（本批）

`pytest tests` 全量 3067 passed / 6 failed / 6 skipped。红的那 6 条就是上一条目里已经在
`d14da4b2` 干净基线上复现过的那一组（`test_engine/test_query_engine.py` 一条、`test_tasks/test_manager.py`
三条、`test_tools/test_bash_tool.py` 一条、`test_tools/test_integration_flows.py` 一条），本批没碰它们
指向的面，本轮未重跑基线；本批**新**红的那条 `test_py310_compat` 已修，见上。
`ruff check src tests scripts` 0、`mypy --strict src` 269 文件 0 问题；
`tests/test_iterate/test_glasspane_ledger_link.py` 现有 28 条（本批 +5）+ `tests/test_py310_compat.py` 补 2 条。
十五发反向变异每发**具名**变红并按 sha256 逐字核回（`glasspane_recipe.py` `043471f1…`、
`reverify_trigger.py` `5e287f84…`、`cli.py` `4b7d4f61…`、`commands/iterate.py` `362be1ed…`；
全程自带备份 `cp`，未用 `git checkout`/`stash`——并行会话正在用主树）：链接前先验链被去掉、
genesis 不钉、轮换后的台账照样被引用、环境变量不再压过链接、坏 JSON 与错形状各自被悄悄忽略、
`os.open` 的 OSError 不再被包、闸的读者读错 key、闸不落进账本行、闸自身另外两发（豁免放宽到
`ImportFrom`、扫描退回单根），再加这一批的门：终端自己编一句拒绝、终端自己编一句缺席、slash 自己
重排成功文本（**只改一个句号也红**）、拒绝不再让 shell 非零退出。

真机读数（`ih`，隔离项目目录，被链接的链取 `tests/fixtures/glasspane_chain_ts_written.jsonl`
那份**真内核字节**）：未链接时打印三条来源与 `0 entries`；链接后 `resolved from: link` +
`chain: verified, 3 entries, 1 of them a passing outcome carrying an operation id` +
`genesis pinned: dl_AAAA…`；`gate` 答的是"那是缺席，不是通过"；被改过一个字段的链拒绝并退 **1**、
点名 `LEDGER_LINK_E_LEDGER_CHAIN`、报出断在第 2 行，而 `.iterate/` **一个文件都没留下**；
落盘的链接文件 `0600`，父目录 `0700`。

## [2.10.0] - 2026-10-24

### Added

- **团队名单：`ih iterate agents` + WebUI「团队」页（`GET /api/v1/agents`，快捷键 `g a`）。**
  引擎其实一直在同时跑着三类工人——子进程 teammate、进程内 teammate、普通 Shell 任务——
  它们全都往同一个 `BackgroundTaskManager` 里登记记录，但那个 store 外面**一个调用方都没有**：
  `list_teammates` / `get_teammate_status` / `active_agents` / `shutdown_all` 四个方法写了却没人用，
  CLI 与 WebUI 都看不到"现在到底有哪些子代理在跑、跑了多久、最后说了什么"，
  更没法停掉其中一个。卡住的那一轮只能翻整份终端输出。
  新增 `swarm/roster.py` 作为纯聚合层：每行带 `agent_id` / `team` / `kind`（进程内 / 子进程 /
  Shell）/ `backend` / `duration_s` / `output_bytes` / 有上限的输出尾部 / `return_code`，
  排序是"在跑的在前、跑得最久的在前"，让眼睛直接落在活着的工人身上。
  - **CLI**：`ih iterate agents` 输出表格，`--json` 给脚本、`--tail/--status/--limit/--max-lines`
    控制体积，`--stop <id> --confirm` 停掉一个（不带 `--confirm` 只提示要确认，不改任何东西）。
    退出码 0/1/2 区分"空名单 / 部分失败 / 用法错误"。
  - **WebUI**：侧栏新增「团队」，KPI 卡（在跑 / 失败 / 已停 / 完成 / 在册）+ 状态筛选 +
    全文搜索（跨身份、团队、任务类型、描述、输出）+ 逐行展开（完整元数据与输出尾部）；
    「在跑」的行走 5 秒轮询，全员终态或名单为空时自动停轮（空的控制台不该一直占着连接）。
    停止按钮走二次确认弹窗，并在后端审计日志里留一条 `agent.shutdown`。
  - **停止是同一套语义**：`stop_agent()` 按"任务 id → 子代理 `name@team`"解析，
    路由到 `BackgroundTaskManager.stop_task`，返回结构化结果
    `stopped | not_found | already_terminal | error | invalid` 而不是抛异常——
    "找不到"是"它已经退出了"的实话，不该报成 500。
  - 名单是**进程内**快照：任务记录只存在该进程的内存里（输出文件才落盘），
    所以「团队」页显示的是**正在为你服务的这个 harness 进程**跑的工人。
    这一点写进了接口文档，避免被读成跨进程全局视图。
  34 条 roster 单测 + 14 条路由测 + 13 条 CLI 测 + 3 条真实子进程集成测
  （真 `sleep` 子进程、真读回输出、真杀掉进程组）+ 前端 21 条 vitest。
- **`KNOWN_STATUSES` 单一事实来源。** CLI 的 `--status` 与路由的 `?status=` 此前各自维护一份
  合法值集合，两边已经不一致（路由收到 `status=exploding` 时回 200 空列表，
  而 CLI 同样输入报用法错误）。现在两处都从 `roster.KNOWN_STATUSES` 取，
  输错一个字母，两个入口拿到同一个 422 / 用法错误，而不是一个安静地给你空页。

### Fixed

- **`engine/query.py`：并行工具调用不再把 `CancelledError` 吞成一条"工具报错"。**
  多工具分支用 `asyncio.gather(..., return_exceptions=True)`，注释里也写明了为什么
  （单个工具失败不能把兄弟协程一起取消掉，否则 Anthropic 会因为缺 `tool_result` 拒掉下一请求）。
  但 `CancelledError` 在 3.11 里是 `BaseException`，同样被收进列表当"数据"返回，
  于是它被包成一个 `is_error=True` 的 `ToolResultBlock`。结果：**操作者按下的"停止运行"
  变成了一个没人在意的小红点，循环照样进入下一次模型调用。**
  现在先判 `CancelledError` 并原样抛出——停止就是停止。真正的工具异常仍然走原来的
  `is_error` 分支，未回复的 `tool_use` 配对语义没有改变。
- **`tasks/manager.py`：跨事件循环的停止请求不再报失败、也不再让进程活着。**
  `BackgroundTaskManager` 是**进程**单例，键是 tasks 目录而不是事件循环——
  所以同进程里第二次 `serve()`、`--reload`、或任何在新 loop 上调用的代码，
  拿到的都是一堆"属于死循环的 future"。`stop_task` 里有三处这样的 await：
  `process.wait()`、`stdin.wait_closed()`、watcher drain。
  跨循环时它们抛 `RuntimeError: ... attached to a different loop` / `Event loop is closed`，
  停止请求因此返回失败，而**进程已经被 SIGTERM 打掉了**——也就是"活儿干了但报告失败"。
  新增 `_wait_for_exit()`：等待 future 失败时退回到用 `os.kill(pid, 0)` 向内核确认进程是否还在
  （只探测、不发信号）；`_close_process_stdin()` 把 `stdin.close()` 也一并包进容错范围
  （原来只有 `wait_closed()` 在 try 里，`close()` 自己就会抛）；watcher drain 对
  "watcher 属于别的 loop"这种永远排不空的情况降级为 debug 日志。
  新增真实子进程集成用例：跨 loop 停止后断言 OS 进程确实消失。
- **`tasks/manager.py`：watcher drain 不再吞掉 `CancelledError`。** 原来
  `except (asyncio.TimeoutError, asyncio.CancelledError)` 把两者一视同仁地记 debug 然后返回。
  但"这个停止请求本身被取消了"就是操作者的停止信号——正常返回会把一个没走完的停止
  报成成功，并把第二次停止掩盖掉。现在超时仍降级，取消则记录后原样抛出。
- **`tasks/manager.py::read_task_output`：大于 12KB 的日志读不出来了。**
  旧实现对**文本模式**的 handle 做 `seek(-max_bytes, os.SEEK_END)`——
  Python 直接抛 `io.UnsupportedOperation: can't do nonzero end-relative seeks`，
  也就是说任何超过 12KB 的任务日志，尾部读取一律失败。改为按二进制 seek+read 再
  `decode(replace)`。`roster._read_output_tail` 用同一套写法，并在截断处打一行
  `… earlier output truncated (N bytes)` 标记，让操作者知道看到的是片段而不是全部。
- **`web/events.py`：仪表盘上"零成本"被永久钉在旧值上。** `_build_status_payload` 用
  `as_float(report["totalCostUsd"]) or checkpoint["cost_usd"]` 做回落，
  于是任何合法的**零**成本 / 零 token 报告都被判为假值，退回到检查点的陈旧数字——
  米表从此不再动。改为存在性判断（与 `status.py` 里写对了的 `_budget_view` 一致），
  两侧都过 `as_finite()`。
- **`web/security.py`：审计日志不再在每次仪表盘心跳时读整个 append-only 文件。**
  `AuditLog.entries` 被 SSE 状态推送反复调用，文件只增不减，读取成本线性增长。
  新增 `_AUDIT_TAIL_BYTES = 1 MiB`，只扫尾部；若 seek 落在行中间，丢弃那半行头部
  而不是把它当一条记录。
- **前端 `formatBytes()` 的假安全写法。** `Number.isFinite(bytes ?? 0) ? (bytes as number) : 0`
  里那个 `as number` 并没有把 `undefined` 变成数字，于是所有比较静默为假，
  缺省体积渲染成 `"NaNMB"` 而不是 `"0B"`。改为先归一化再判断，并用测试钉住
  （`undefined` / `null` / 负数都要落到 `0B`）。顺带全文扫了同类模式，其余三处
  （`Dashboard.formatTokens` / `formatUsd`、`BudgetRate.formatTokens`、`RunStatusCard`）
  都是先判后用的正确写法。

### Changed

- `pyproject.toml` / `src/iterate_harness/__init__.py` / `npm/package.json` /
  `frontend/web/package.json`（+ lock）对齐到 2.10.0；`frontend/web/dist` 重新构建
  （hatch 是 force-include 打包它，只改源码会发出旧控制台）。

## [2.9.0] - 2026-10-23

### Added

- **`ih iterate trend`：把「这一轮到底变好了没有」变成一条可查询、可门禁的事实。**
  趋势库一直只有一个 `file|line|dimension` 指纹的当前状态，`log --trend` 也只把它压成
  tracked/open/fixed/stubborn 四个数；"最新一轮新增了什么、修掉了什么、有没有把修过的
  问题又带回来" 这个问题在 CLI 与 WebUI 里都**无法回答**，尽管决策日志里每条 `report`
  条目早就带着完整 findings。新增 `iterate/trend_report.py` 从日志还原逐轮历史：
  - `new` = 本轮有、上一轮没有；`fixed` = 上一轮有、本轮没有；
  - `regressed` = 本轮出现，且在**更早**某一轮被修复过（按整段历史判定，不是只看上一轮）；
  - `unchanged` = 两轮都在；首轮全部计为 `new`。
  命令行支持 `--history/--top/--json`，以及两个 CI 门禁：`--fail-on-regression`
  （最新一轮出现回归即 exit 1）与 `--fail-on-new <none|low|medium|high|critical>`
  （最新一轮新增达到该严重度即 exit 1）。没有历史时输出明确下一步（先跑一次循环），
  不是空白或 traceback。旧版 `log --trend` 保持原样，不破坏脚本。
  日志里的历史形状（`findings` / `topFindings` / `notableFindings` / 嵌套 `summary`）
  都走既有 `findings_from_report`，损坏条目与不可跟踪 finding 跳过而不是让整页崩溃；
  列表长度有上限（计数仍精确），避免几千条 finding 把 API 响应撑爆。
- **WebUI 新增「趋势」页（`GET /api/v1/trend`，快捷键 `g t`）。** 这是引擎能力第一次
  真正接到管理台，而不是再包一层 CLI 文本：卡片区展示运行数 / 跟踪中 / 未解决 / 顽固 /
  可见窗口净方向；「最近一轮变化」按回归 / 新增 / 修复三列列出文件、行号、维度、严重度；
  「运行历史」表逐轮列出 total/new/fixed/regressed/unchanged，有回归的行高亮；
  顽固问题与未解决问题各有一张表，并按维度给出未解决计数。页面在 SSE 报告新决策条目时
  自动刷新（运行中无需手动 reload），也有显式刷新与失败重试。只读接口，不写 `.iterate`。

### Fixed

- **`trend_store.record_run` 的读-改-写终于加锁了；并发收尾不再丢 finding。**
  2.7.0 只把**写文件**换成了临时文件 + 原子 replace，读和写之间仍然没有任何互斥：
  两个进程（例如 WebUI 与 TUI，或两个 swarm worker）同时收尾时都读到旧库、各自保存，
  后写者把先写者的记录整段覆盖掉——文件始终是合法 JSON，所以没有任何报错，只是趋势
  永久少了一条。现在整段临界区用 `sidecar_lock_path` + `exclusive_file_lock` 串行化，
  与其他共享注册表（credentials/settings/mailbox）一致。新增的并发用例把 `load_library`
  拉长 100ms 并让两个线程同时进入：去掉锁时稳定只剩 1 条记录（复现丢更新），加锁后
  `a.py`/`b.py` 两条都在。
- **SOCKS 代理用户不再在启动阶段直接崩掉。** `httpx`（OpenAI/Anthropic SDK 共用）默认
  读取 `ALL_PROXY`/`HTTPS_PROXY`；当环境里是 `socks5://…` 而没装 `socksio` 时，客户端
  构造就抛 `ImportError`，用户看到的是一段 SDK 栈，harness 根本起不来。新增精确锁版依赖
  `socksio==1.0.0`（纯 Python、无传递依赖），SOCKS 代理可直接使用。这也是本机全量测试里
  7 条 `test_openai_client` / hooks 用例失败的根因。
- **`tests/test_tools/test_bash_tool.py` 的 drain 用例不再看机器负载脸色。** 测试替身在
  第一次 `read()` 里 `sleep(0.08)` 后返回 `b""`，本意是"错过 50ms 读窗口"；但真实
  `StreamReader` 只在 EOF 返回 `b""`，负载高时事件循环先跑完 sleep，drain 正确地把它
  当成流结束而提前返回，用例随即红。改为抛 `asyncio.TimeoutError`（与 `wait_for` 的
  超时语义一致），让"第一次读超时、第二次拿到 late burst"在任何调度下都成立；生产代码
  未改（它把 `b""` 当 EOF 是对的）。
- **工作树里跑 `pytest` 不再去测另一棵树的代码（这条让此前的绿是假的）。**
  harness 是 `pip install -e` 装的，editable 记录把 `iterate_harness` 固定到**某一个**
  checkout；在别的 worktree 里 `cd harness/iterate-harness && pytest tests/ -q` 时，
  测试文件里的 `from iterate_harness import ...` 解析到的是那个被固定的 checkout 的源码。
  于是本版本的工作树实测**根本没能被测到**：全量用例如期跑到最后一步，报
  `ImportError: cannot import name 'trend_report'`（新模块只存在于工作树里），
  而此前任何一次绿灯都无法证明是这棵树的代码在跑。`[tool.pytest.ini_options]`
  显式加 `pythonpath = ["src"]`，让"你在哪跑"就等于"测的是哪"，CI 与工作树一致
  （与 mypy 早已用 `mypy_path = "src"` 规避的同一陷阱）。
- **`trend_report` 不再把布尔值当成行号或计数。** Python 里 `True` 是 `int` 的实例，
  所以 `line: true` 会被认成第 1 行、`totalFindings: true` 会被当成 1 条 finding——
  而趋势库的指纹对同样的 `true` 一律按"文件级、无行号"处理（`finding_fingerprint`
  显式拒绝 bool），于是同一个 finding 在趋势报告里显示成 `file:1`、在趋势库里显示成
  `file`，两个视图对不上，行号列还会冒出字面 `True`。现在两处口径一致：`line` 为布尔
  时按"无行号"处理，`totalFindings` 不是真整数时回落到该条目实际还原出的 finding 数。
- **`python -m iterate_harness.cli` 不再静默空跑。** 应用此前只在 `__main__.py` 里接上，
  所以按模块路径调用时导入成功、什么都不做、退出码 0——看起来像"跑完了"，其实什么都没
  执行。现在模块路径与 `python -m iterate_harness` 行为一致。

### Changed

- `.gitignore` 的 `node_modules` 去掉尾斜杠。带斜杠的 `frontend/web/node_modules/`
  只匹配**目录**，而共享 checkout 的工作树里 node_modules 是一个**符号链接**（git 眼里
  是文件），于是它一路显示为未跟踪，任何一次 `git add -A` 都可能把一个 200MB 的安装
  目录推上发布仓。
- `frontend/web/package.json` 与 lock 从落后的 2.7.0 对齐到本次版本；此前 2.8.0 发版
  只 bump 了 Python 包与 npm 包装器，前端元数据留在旧版本，构建产物与发布记录对不上。

## [2.8.0] - 2026-10-22

### Fixed

- **`tasks/manager.py`：投递监视器不再把已经干完的活儿重跑一遍。** 监视器判断"payload 有没有被读"
  的唯一信号是写入之后输出文件是否增长，所以基线必须在写入**之前**采样。旧实现先 `sleep` 100ms
  再采基线——一次快得可怜的 worker（`python -u -c "import sys; print(sys.stdin.readline())"`）
  在这 100ms 里已经把回显写进文件，于是回复被算进"写入前的旧输出"，监视器判定"没有新输出＝
  payload 被吞了"，把一条已 `completed` 的任务拉回 `running`、重新投递、把输出重复一份、
  `SUBAGENT_STOP` 触发两次。实测 6/6 复现（`restart_count=1`，输出里多出一份 restart 告示）。
  现在基线在写入前采，并加一个有上限的"安静轮询"（`_OUTPUT_BASELINE_SETTLE` 50ms /
  `_OUTPUT_BASELINE_POLL` 10ms）等异步 copier 把最后一块刷完，避免把**写入前**但还没落盘的
  输出误判成回复；两种失败方向都被压到亚毫秒量级，而不是原来的 100ms 窗口。
  反向也钉住：真正被吞的 payload 仍然会被重启补投
  （`test_delivery_monitor_still_redelivers_a_swallowed_payload`）。
  两条新用例都经过反向变异验证——把源码改回旧实现时
  `does_not_restart_a_completed_one_shot_agent` 当场红在 `assert 'running' == 'completed'`，
  与最初的失败现场一致。

- **`personalization/extractor.py`：`git_remote` 终于能抽出远程地址了。** 旧正则
  `(?:github|gitlab)\.com[:/](\S+?)(?:\.git)?` 里 `\S+?` 是懒惰的、后面又跟一个可选的
  `(?:\.git)?`，于是永远只匹配一个字符——`github.com:jingzhao-l/iterate-harness.git` 抽出
  `'j'`，然后被 `len(value) < 3` 的守卫静默丢弃。结果是这一类事实**从来没被记过**，
  而 `_FACT_PATTERNS` 里其余九条都正常。改为 `((?:[\w-]+(?:\.[\w-]+)*/)+[\w-]+)`：
  最后一段刻意不允许点号，这样贪心匹配吞不进 `.git` 后缀；带点号的 owner
  （`owner.name/repo`）仍能匹配。实测：`.git` 后缀 → `jingzhao-l/iterate-harness`，
  无后缀 → `foo/bar`，`gitlab.com/team/api.git` → `team/api`。5 条新用例，
  反向变异（把正则换回旧写法）5 条同时变红。

- **`personalization/extractor.py` / `session_hook.py`：抽出本地规则不再因坏历史崩掉，
  也不再是一份没人调的副本。** `extract_local_rules()` 直接对 `msg.get("content")` 取值，
  历史上任何一条不是 dict 的条目（截断的记录、手工改过的存档、`[]`、`None`、裸字符串）都会
  抛 `AttributeError` 把整个会话收尾钩子打断；content 是 list 时也没有校验 block 里的
  `text` 是不是字符串，`[{"text": None}]` 会在 `"\n".join()` 上炸 `TypeError`。
  另外它一直挂在 `__all__` 里作为公共 API，但**没有任何调用方**——真在跑的是
  `session_hook.update_rules_from_session()`，它自己写了一份几乎相同的收集循环。
  现在把收集逻辑提成 `_collect_message_text()`，两种形态（线上 dict 形状 / 内存里的
  `ConversationMessage` 对象，block 的 `text` 或 `content` 字段）走同一份实现，
  非 dict 条目与类型不对的字段一律跳过而不是抛异常；`extract_local_rules` 与
  `session_hook` 都改用它，死代码清零、行为收敛到一处。7 条新用例覆盖 dict 形态、
  对象形态、各类畸形条目与非法入参。

- **`iterate/loop_gates.py` / `loop_policy.py`：两处 mypy 一直在报的类型洞补上，
  而且"闸门已测量"与"没有阻塞"被收成同一个判据。** `loop_gates.py` 从 `.types` 导入
  `Finding`，而 `types.py` 里只有 `ReviewFinding`——`mypy src` 因此恒定报
  `has no attribute "Finding"`；改为真实类型，`metadata` 也顺带从 `dict[str, Any]` 收紧到
  `dict[str, object]`。`loop_policy.py` 的 `_kernel_decision` 把
  `snapshot.invariant_state`（`str | None`）和 `evidence_complete`（`bool | None`）直接喂给
  `RoundState`，而 `RoundState` 要的是四个判决字的 `Literal` 联合与真正的 `bool`——mypy 恒报
  `arg-type`。根因是"没有阻塞 ⇒ 两把闸都已测量"这个不变量只存在于
  `kernel_blockers()` 的返回语义里，type checker 看不穿那个 helper，于是拿一个 `str | None`
  冒充 `Literal`。现在把收窄点提成 `_validated_snapshot()`（`kernel_blockers` 与
  `_kernel_decision` 共用，两个答案必须一致），返回值标成 `tuple[InvariantState, bool] | None`，
  `AggregateSnapshot.invariant_state` 本身也改标成 `InvariantState | None`——
  唯一的产出方 `_read_invariant_state` 只会给出那四个合法值，标 `str` 是承诺了快照实际持不住
  的东西；该函数里的 `in` 成员判定是真正的运行时守卫，`cast` 只是把守卫已经证明的事实说给
  mypy 听（mypy 无法凭 `in` 对字面量元组的判定收窄 `str`）。`mypy src` 由 2 error 归零，
  260 files 干净；`ruff check` 同时清掉 `tests/test_services/test_compact.py` 里一个未使用的
  `TextBlock` 导入。

- **npm 包装器：无人值守的首次运行不再自动确认下载；stamp 现在记得运行时是"怎么装出来的"。**
  五条各不相关的缺陷一起修掉：
  ① `askYesNo(question, defaultNo=true)` 的超时/EOF/空输入回落到 `defaultNo`，也就是
  **true＝继续**——而提示打印的是 `[y/N]`。无人值守（`< /dev/null` 或 120s 无回应）的
  首次运行会**自动确认几百兆的下载**，用户从来没同意过。现在回落值统一取 `!defaultNo`，
  与显示出来的默认一致，提示也写明回落到了哪一个默认值；原先把错误语义钉成正确的那条
  测试已经反转并扩充为 7 条。
  ② stamp 以前只记版本字符串，所以换掉 `ITERATE_HARNESS_PYTHON` 或 `ITERATE_HARNESS_INSTALL_URL`
  后 stamp 仍然"匹配"，包装器会静默复用一份不是用户当前配置装出来的运行时。
  现在 stamp 是 JSON（`version` + `pythonEnv` + `source`），三者任一变化都触发重装；
  老的裸版本 stamp 标记为 `legacy`，版本匹配时重装一次并重写成新格式——升级本身就会因
  版本变化而重装，所以这条路径不额外付出成本。
  ③ `packageVersion()` 在 `package.json` 读不出时返回 `"0.0.0-unknown"`，而
  `needsBootstrap` 永远判定"过期"，于是一次注定失败的
  `pip install iterate-harness==0.0.0-unknown` 在**每一次**调用上都跑一遍。现在版本号不可读时：
  有现成运行时就复用并提示，没有则以可执行的报错退出，而不是尝试一个不存在的版本。
  ④ `DOWNLOAD_TIMEOUT_MS`（120s）大于 `DOWNLOAD_TOTAL_TIMEOUT_MS`（60s），按请求的超时分支
  永远不会触发（总时限一定先到）。下调到 30s，让那条分支重新可达。
  ⑤ 包装器参数只应在 `--` **之前**属于包装器，`ih sub -- --no-verify` 以前会把
  转发给子进程的标志吞掉（抽成 `splitHarnessArgs()` 便于单测）；以及 `reportBootstrapFailure`
  与 `child.on("error")` 里的 `process.stderr.write` 在错误处理器内部可能再次抛错，
  把已处理的失败变成未处理的异常——包上 `safeWriteTo()`。
  npm 测试从 76 条扩到 89 条全绿。

- **`pyproject.toml`：把 `mcp` 的 Dependabot 升级回收到源头，而不是在发版时把它回退掉。**
  独立发布仓 `jingzhao-l/iterate-harness` 已接受 `mcp 2.0.0 → 2.2.0`，但本 subtree 仓库把
  `uv.lock` 列为 gitignore，那处 bump 没有 lockfile 跟随、也就没回流到这里，于是
  **产物比它自己声明的依赖新**（独立仓是 2.2.0，源头是 2.0.0）。发版同步 tree 时若照抄
  源头，就会把那处已接受的升级静默回退。改为在源头补上升级：2.2.0 里
  `mcp.server.mcpserver` 从模块变成了包，但包的 `__init__` 仍从 `.server` 重导出 `MCPServer`，
  客户端 `mcp.client.stdio.stdio_client` 与
  `mcp.client.streamable_http.streamable_http_client` 入口未变——`src/iterate_harness/mcp/client.py`
  与 `tests/test_mcp/` 用到的每个符号都逐一导入验证过。全量 2879 passed，与 2.0.0 读数一致。

### Added

- **#25 见证 stash 的并行覆盖窗口修掉，并把"为什么 fork 那侧不核"写进函数注释。**
  折叠规则只认"新键或换了对象"，于是同一轮里两个并行 MCP 调用各自从"没有见证"的浅快照起手、
  各建一个 dict，后折回的整键盖掉前一个——第二次的证据在，第一次的没了，方向仍是漏判不是误判，
  但"漏判"本身就是这条核对存在的理由。修法是在 `engine/query.py` 的折叠循环里对这一个键做合并
  而不是替换（键名与上限从 `dimension_ledger` import，引擎不重述字符串；合并后再按上限淘汰最旧，
  免得一轮里几十个兄弟调用把每一轮都要携带的 dict 撑大），引擎对其它键的"不回填旧浅拷贝"规则一字未动。
  用例里那道**屏障才是这条测试的本体**：不加屏障时两个调用实际是一先一后跑完的，第二个快照到第一个
  的 dict 并原地改，于是断言无论折叠怎么写都绿——那测的是调度器不是规则；强制两次调用同时在飞之后
  把合并还原成替换，那条用例当场点名红（`len(arrivals) == 2` 也先挡住"没重叠也算过"这种滑过去）。
  另一处是防止下一个人重走我的弯路：`echo_contestation` 的注释里点名 fork 那侧
  （`plugin/glasspane-decision-log.ts` 从引擎回包的 pack 记决策、维度取自同一次调用的 metadata，
  声明与记录同源），照搬过去就是"拿一个值和它自己比"——#23 那版就是这么死的，别再补一遍。
  `tests/test_engine` + `tests/test_iterate` 1272 passed，唯一那条红（`test_subagent_stop_hook…`，
  `return_code=127`）在 pristine 基线同样红，是本机没有 `python` 可执行文件的既有环境项。
- **#24 交叉核对搬到见证侧：#23 那个"账本侧裁决"在生产里永远跑不到，这次换成 agent 自己那一次调用。**
  先记清为什么搬。#23 把判定放在 ledger 的 `reverification` 行上（声明 X、壳回 Y ⇒ 扣住不计），落码后回
  读发送方才看见死路：那条路上唯一可能写进回显的发送者就是 ① 自己，而 ① 要声明某 op 服务哪个维度，
  只能把账本里那条归属抄回来——被拿来核对的正是被核对的东西，比较永远相等；不抄就没有回显。于是
  "沉默不算异议"这条我自己写下的规矩，反过来证明了那个读者没有生产者。**删掉裁决，保留引用**：
  `echoed_dimension_map` / `contested_attributions` / `_uncontested_counts` /
  `dimension_context_with_contested` 一并移除，覆盖率那行回到原状，`echoedDimension` 仍随 ① 的行写进
  ledger（它是"壳当时说了什么"的旁证，与 `annotations` 同一性质，不接任何判决），键名改由
  `dimension_ledger.ECHOED_DIMENSION_DATA_KEY` 一处拼写、写侧 import。
  新的读者放在真正有生产者的那条缝上：`tools/mcp_tool.py`——agent 自己调 `gp_*` 就是它。改动三处，
  各自有能红的对照：(1) 适配器改吃 `call_tool_parts`，交给模型的仍是 `call_tool` 那句一字不差的
  join（用例把两种视图逐字节比过），因为回显是**独立 content item**，拼平了就再也分不出来；
  (2) `record_observed_echo` 把见证按 op 存进 run 的 tool metadata（与 `iterate_state` 同一条 durable
  通道，才能在后面那一笔写账本时还在），有界 64、滑窗淘汰最旧——不设界的 dict 会跟着每一轮折叠；
  (3) `iterate_decision_log` 的 append 在 `attribution_refusal` 之后查 `echo_contestation`，声明与见证
  不一致就**拒写**、返回 `KERNEL_E_DIMENSION_CONTEST`，remedy 给出两条都能执行的路（按见证的维度记，
  或重新按你要的维度读一次再引那一次），并且**不许改前一次调用的声明**——它就是这次核对用的证据。
  不一致以外的全部情形都放行：没有见证、见证属于别的 op、以及声明与见证一致。核对不替账本凭空加一个
  维度的证据，这一点与 #23 同一条边界。
  前缀守卫也有读者：只有存在 `dimension: ` 开头的旁项时才进分类器，普通工具回话不吃那次 JSON 解析；
  探针用"记账式 spy"而非抛异常的 spy——适配器刻意兜住见证失败不让它打断 agent 的工具回话，会抛的
  探针正好被那道兜底吞掉，那样测不到"从没被调用"这句话（这条是本轮变异教我的，见下）。见证失败也从
  debug 提到 warning：坏掉的见证若静默停摆，所有走回话的用例照样绿。
  真机对照（2026-10-22，两个壳都是本机真物，op 号取自 `~/.glasspane/evidence` 里真实落盘的文件名）：
  今天从 main 打出的 bundle 广告 `dimension`、真回话里带出那行回显、见证写下
  `{op_01M35PAMYNNPJRNCCM6Q2C34XQ: security}`，随后用 "correctness" 去记决策被拒；而 `/usr/local/bin`
  那个旧壳不广告、无回显、无见证，核对放行——沉默不算异议在生产形状下同时被两侧测到。顺带量到一件事：
  这台机器的 daemon 自报 **1.6.2**，它的 `capabilities` 还是修好之前那份手抄表（多 `probe`、缺
  attach/last_evidence/probe_status），所以壳那句"该调用会被答 methodNotFound"与"last_evidence 真的
  答了 pack"同时成立——**读得对、后果说过头**，源里清单已由 `EngineMethod` 派生，父仓下一次发布即消失；
  这不是新缺陷，但它是"漂移告警要拿在跑的构建去核"的一条实例。
  测试：`test_dimension_echo.py` 15 → **21** 条。新加的两条走的是 `engine/query._execute_tool_call`
  本体，不是把手工 stash 递给被测函数：先看 MCP 那一调把见证折进 `context.tool_metadata`，再看同一
  context 上的 `iterate_decision_log` 因此被拒、而换成一致的维度就写得进去——上一版栽的正是"函数单测
  绿、状态没人搬"。顺带修了两个假件的形状：`_FakeMcpManager` / `FakeMcpManager` 只有 `call_tool` 而
  真 `McpClientManager` 两个视图都有，适配器改吃 parts 后它们当场 AttributeError——**假件缺哪个方法，
  就该补哪个方法，而不是让产品代码去迁就假件**，两边现在保持"join 就是 parts 的拼接"这个真实关系。
  变异九发逐一点名变红（适配器不再见证／模型字节改形／前缀守卫拿掉／写时核对删除／把别的 op 的缺席
  当异议／一致也当异议／上限不淘汰／收下非法 key／① 的行停发引用），按自带备份 + sha256 核回。P5
  第一版打不动东西：我那条"沉默当异议"的变异改的是*已有 stash* 那一支，而对应用例根本没建 stash——
  换到有 stash 的那条上才红，这条也记着。
  归属留档（这一轮量了三次，每次都换说法，记清楚）：第一次全量 16 失败里 8 条（openai client /
  reasoning effort / untested_features）是这台机器 shell 带着 HTTP(S)_PROXY 时 httpx 建 transport 就炸
  的噪声，清掉代理变量后基线（`origin/main` `82369b99`）与我这棵树跑同一子集都是同样 3 条既有环境失败
  （`test_tasks/test_manager.py`，本机没有 `python` 可执行文件），另 2 条就是上面那两个假件——**"我
  改红了套件"与"这台机器的代理改红了套件"是两句话，得分别量**。干净环境再全量一次：2876 passed /
  9 failed；把那 9 条单独再跑做对照，我这棵树红 6 条、基线在同一份名单上红 **7** 条，全落在那一族
  环境项（无 `python` + 负载下的计时）里，本批一条也没新弄红。ruff check 干净。落 iterate-skill main。
- **#23 面 A 的回显有了读者：交叉核对只减不加，缺回显永不算异议（场景②/⑧ 的补账）。**
  上一批给面 A 装了 `dimension` 槽并让它把声明回显成独立 content item；如果没人读它，那行字就只是
  多一块表面——本仓反复把这种形状叫"没交付"。现在它有读者，且读者只做一件事：**怀疑**，不裁决。
  `relay_answer` 从旁项里认出 `dimension: <id> | …` 这一行，填进新的
  `RelayedAnswer.echoed_dimension`，而**该行仍原样留在 `annotations` 里**（读出≠吃掉，模型照旧看得见
  壳写的每一个字）；`reverify_trigger._relay_answer` 把它作为结构化字段
  `echoedDimension` 同时写进这一轮的载荷与 ledger 那一行，读者因此不必去刮一句它没写过的话——写侧与
  读侧共用 `dimension_ledger.ECHOED_DIMENSION_DATA_KEY` 这一个键名常量，两侧对不上时红在读侧而不是
  一起静默失配。
  `dimension_ledger` 新增 `echoed_dimension_map` / `contested_attributions` /
  `dimension_context_with_contested`：一条决策把 op 归给维度 X，而壳为同一 op 的回答回显了 Y≠X，
  这条归属就被**扣住不计**，该维度重新读作 unverified——并且扣了几条写在同一行覆盖率话术里
  （`_dimension_coverage_line` 只在真有扣计时追加那句，没扣计时那行字节一字不动）。三条边界是这批的
  全部要点：**缺席的回显永不算异议**（面 B 把声明放在 metadata 里，那里根本没有这行字，把沉默读成
  不同意就是拿一侧的写法去判另一侧）；**畸形的回显算沉默而不是另一种主张**（id 过内核校验器才认，
  过不了就不认，绝不让一个坏值变成反驳）；**交叉核对只减不加**（一句回显永远不能替账本凭空记上一个
  维度的证据）。`project_dimension_context` 与 `dimension_context_with_contested` 从同一次读盘产出
  结论与扣计数——两次读盘会让"数字"与"它描述的那份账"不是同一份，那是比不报数更坏的东西。
  新增 `tests/test_iterate/test_dimension_echo.py` 15 条：证据包取自随包语料
  （`tests/kernel_fixtures/evidence-decision.ok-01.json`）而不是手写形状，ledger 行由真实记录器
  `record_reverification_run` 写出而不是拼字典——写侧停发这个字段时读侧用例点名变红，而不是两侧一起
  跟我的猜测达成一致。变异七发逐一点名变红（不提取／把旁项吃掉／收下畸形 id／关掉交叉核对／把沉默
  当异议／结论不再扣减／写侧停发字段），按自带备份 + sha256 核回。`tests/test_iterate` 与压缩顺序套件
  合计 1221 passed，ruff 干净；回显行的形状来自父仓 `mcp-shell/src/dimension.ts`，因此本侧读得宽、
  认得严：措辞漂了最多是让交叉核对不发生，不会把判决读反。
  真机走查（2026-10-22，本机 /usr/local/bin/glasspane-mcp，非夹具）：McpClientManager 连上已安装的那个壳
  （广告 16 个工具），在引擎 loop 上 await 这条新通路发了一次真实回验——gp_last_evidence 回 answered 并
  带上被引用的 op 号，gp_diagnose 回 refused 且用的是 daemon 自己的 GP_E_NOT_ATTACHED 与它自己的 remedy
  （"call attach first"，一字未改），载荷 attempted=True / verified=True / clean=False，账上多出一条
  reverification 归属行。两条回显字段都是 None——本机装的这个壳还是面 A 那批之前的构建，它还不写那行字，
  于是新读者读到的是沉默而不是异议，这正是该行为在生产形状下的证明。反过来讲：回显这条链的端到端
  （面 A 写 → Python 读）目前只有单元与变异证据，要等父仓把带槽的壳装上本机才算真机闭合。
- **#22 场景①真正接通：发布点改为 await，宿主一次绑两份 transport——并如实记下上一批那份"绑了但跑不到"。**
  回代码复核 #21 的落地形状（不是推测，是读到的行）：`engine/query.py:801` 与 `:1521` 用
  `await tool.execute(...)` 驱动工具，`ui/runtime.py:298-300` 在引擎 loop 上 `await connect_all()`、
  `:424` 把同一个 manager 放进 tool metadata ⇒ 发布点**恒**在拥有 MCP session 的那个 loop 内。
  `make_host_transport` 的接受分支只在"本线程无 running loop"时才走，因此它在生产里一次也没跑到，
  每一轮读回的都是 `GP_E_HOST_LOOP_BUSY`。更要紧的是当时那条端到端用例把这句拒绝**断成了期望行为**
  （`{answer["code"]} == {LOOP_BUSY_CODE}`）：闸是绿的，通道是死的——"测试通过"与"能力存在"是两回事，
  这一处正是本项目反复告诫的那类假绿。跨 loop 驱动也不行（session 绑定在连接它的那个 loop 上），
  所以唯一的诚实出路就是本批做的：在拥有客户端的循环上 await。新增 `make_host_acall(manager, server)`
  ——同一份 deny-by-default 只读集合（`READ_ONLY_TOOLS` 从契约 import，不复述名字）、在 await 之前拒、
  消费 `call_tool_parts` 交 `list[str]`；有意不再套一层 `asyncio.wait_for`：客户端自己的
  `_bounded_await`/120s 决定错误形状，宿主加第二个计时器只会改写"该报哪个超时"。
  `bind_host_transport` 改为一次发现绑两份（`GLASSPANE_TRANSPORT_KEY` + `GLASSPANE_ACALL_KEY`），
  键串只在 `reverify_trigger` 定义一处并由宿主 import，具名用例数 `src/` 里该字面量的出现次数来防第二份
  契约漂移。`trigger_reverification` 拆成 `_prepare`（能不能问、问哪些、拒绝怎么转述）+ `_finalize`
  （发布载荷与 ledger 那一行），同步/异步两个触发器只差"怎么驱动"，规则正文不再有两份。
  `_sync_transport` 现在过滤 coroutine 函数：把 awaitable 误绑到同步键上，原先会被真的调用、拿回一条
  `GP_E_ASYNC_HOST` 拒绝并**以引擎回话的形状**进入 relay——本项目不接受这种冒充。
  端到端断言改成立：真实 `tool.execute` 路径下 `attempted=True`、两条答案 `state=answered`、
  `code` 集合恰为 `{None}`、帧走 `call_tool_parts` 且 `joined_calls==[]`、三个 content item 里两条旁项
  原样进 `annotations`、`verified=True` 而 `clean=False`（引擎说"claim still does not hold"不会被这侧洗白）、
  ledger 恰多一条、`invariant_state` 仍是 `fail` 且不因回验成功而变 measured。另加
  `_LoopPinningClient` 断"驱动 session 的 loop 就是连接它的那个 loop"，跨 loop 这条错误出路从此是失败
  断言而不是挂起；同步 transport 保留给无 loop 的宿主（CLI / 测试体），其在循环内的具名拒绝用例一字未改。
  变异四发各点名一条用例变红（异步触发器无视 acall／acall 放弃只读拒绝／同步触发器把协程函数当
  transport／宿主只绑同步那份），按自带备份 + sha256 核回；第一版 M3 打不动任何用例，因为我那条负例把
  awaitable 绑在**异步键**上、同步触发器根本不看那个键——重写为绑错到同步键上之后 M3 当场点名变红，
  这条账记在这里是因为"我的负例测的是自己写的键"正是假绿的另一种形状。全套件 2849 passed / 6 skipped /
  4 failed——那 4 条在 pristine main（`3a0dc70d`）隔离复跑同样红，是本机无 `python` 可执行文件的既有环境
  失败（`return_code=127`），与本批无关。`test_host_transport.py` 收集数 23 → **31**（新增 8 条）。
- **#21 场景①的宿主半：harness 自己绑定同步只读 GlassPane transport，失败判决真的送达引擎。**
  `reverify_trigger` 的规则 5 一直如实记录 "not attempted"，因为这个仓库里没有人绑过 transport——
  `McpClientManager` 是 async，`run_reverification` 是 sync sender。新增
  `iterate/glasspane_host_transport.py`：`bind_host_transport(metadata)` 从 run 已有的
  `mcp_manager` 造出 transport 并绑到 `GLASSPANE_TRANSPORT_KEY`，`_aggregate` 只在合并副本上绑，
  callable 绝不进入跨轮折叠的 durable `tool_metadata`。四条构造性约束：(1) 只读——工具集合
  `from glasspane_reverification import READ_ONLY_TOOLS`，不复述名字，且写工具在协程创建之前就被
  拒（stub 记录零帧），拒绝走 shell 自己的 `CODE: msg | remedy: R` 形状 `GP_E_HOST_WRITE_TOOL`；
  (2) 边界——消费 `call_tool_parts()` 交给 `relay_answer` 的是 `list[str]` 而非 join（具名测试拿
  join 后的同样字节反证 `unparseable`）；(3) 再入——发布点在引擎 loop 内，故分两种可观测行为：
  本线程无 running loop 就 `asyncio.run(wait_for(...))` 有界驱动（窗口 150s 大于客户端自己的
  120s，让客户端错误形状先赢），本线程已有 running loop 就**不发**、返回 `GP_E_HOST_LOOP_BUSY`
  并把 remedy 写成宿主可执行的两条出路（为回验单独配 stdio session，或由拥有 client 的 loop
  await `run_reverification_async`），绝不 `asyncio.run` 撞 RuntimeError 再吞成假答案；(4) 传输错误
  与超时保持 `McpServerNotConnectedError`/`McpServerTimeoutError` → relay 成 `GP_E_CHANNEL` 带 remedy。
  可观测性补齐：`GLASSPANE_TRANSPORT_ABSENT_KEY` 让"没绑上"读成 not attempted, because X（无 mcp
  plane / 没有 server 广告只读工具 / server 状态是 failed），而不是沉默也不是空成功。ledger 与
  gate 规则一字未动：无判决仍不发布任何键，回验永不使 gate 变 measured——具名测试断言 published
  state 的 gate 键集合恰等于 `loop_gates` 测出的那个，且拒绝路径写零字节、真实发送恰一条归属条目。
- **#20 多 item 边界：MCP 客户端不再销毁 shell 的 content-item 边界，回验 relay 改逐 item 分类。**
  实测（2026-10-21，对当时那份代码直调）：`mcp/client.py` 把 `result.content` 用
  `"\n".join(parts)` 拼成一个字符串，`glasspane_reverification.relay_answer` 拿到"daemon 帧 +
  任何第二项"一律判 `unparseable`——而 shipped `glasspane-mcp` 的 `gp_diagnose` **今天就回多项**
  （帧 + `update state:` 块，见 GlassPane `mcp-shell/test/update-state.test.mjs`；2026-10-20 批次
  又加了 `provider: … | declared prerequisites: …` 归属行）。这不是新发生的，是早已成立，只是没有
  真形状测试。修法在消费端：`McpClientManager.call_tool_parts()` 返回逐 item 的 `list[str]`
  （非 text 项、`structured_content`、`(no output)` 兜底逐项保留，不发明新政策），`call_tool`
  改为它的 `"\n".join(...).strip()`——LLM 看到的东西一字不变，具名测试钉住字节相等；错误面
  （`McpServerNotConnectedError`/超时）不动。`relay_answer` 接受 `str` / `Mapping` / `Sequence[str]`：
  多 item 时选"第一项能 parse 成 JSON 对象或匹配 shell 编码拒绝形状"的那项做判决，**其余每项
  原样存进 `RelayedAnswer.annotations`** 并由 `to_model_lines()` 逐条逐字展示（update 块和归属行
  是关于这次调用的用户可见事实，丢掉与读不到帧同错）。`_refusal_from_text` 从此只作用于单项
  （docstring 补了多 item 的实测句）。joined `str` 输入**保持** `unparseable` 语义、绝不按换行
  重切——合法帧的文本自己就带换行，重切是猜边界，负控制测试钉住这条。`reverify_trigger` 的宿主
  传输契约改述为 parts 形状（返回 `list[str]` 或纯 `str` 都合法），无判决则依旧一言不发。
  综述 §8.5 场景③"无 CLI 文本解析"由此真正成立：判定路径上没有任何文本拼接再解析。
  具名测试：`tests/test_mcp/test_call_tool_parts.py`（1/2/3/padded item 的 join==parts、非 text 项、
  兜底、错误面）；`test_glasspane_reverification.py`（帧+归属 answered、归属在前的乱序、
  evidencePack+第二项仍可转录、拒绝+注记、全 unparseable 不造判决、多行帧仍解析、model lines
  每条注记恰好一次、joined 负控制）；`test_reverify_wiring.py`（parts 传输到 round 与账本、
  str 传输零行为变化）。


### Changed

- **内核 `iterate-kernel@0.1.9`：`zod` 从范围改成精确版本，因为发布出去的 `.d.ts` 是按那一个版本发光的。**
  0.1.8 及之前声明 `"zod": ">=3.25.76 <5"`，而 lock 实际解析到 3.25.76、`.d.ts` 也是 zod 3 形状
  （`ZodObject<Shape, "strict", z.ZodTypeAny, Out, In>`）。消费者一侧的解析器只要挑了 zod 4，拿到的不是
  类型错误而是**类型消失**：`z.infer` 静默退化成 `any`，TypeScript 反而去抱怨消费者自己的回调
  （`TS7006: Parameter 'ref' implicitly has an 'any' type`）——编译过、类型没了，两侧都绿。这不是推测：
  GlassPane 的 `mcp-shell` 改吃发布包时当场撞上，它在下游用 npm `overrides` 把整棵树钉住才绕开。
  上游的修法是把声明收成事实：`"zod": "3.25.76"`，并加 `kernel/test/zod-contract.test.mjs` 三条判据
  让"声明与发光版本不一致"变不可发布（精确性、装机==声明、`.d.ts` 仍是 zod 3 形状）。反向变异三发
  各打中具名用例，收尾 sha256 核回。将来真要换 zod major 是一次契约事件：release note 里点名、
  与这条 canary 同批改，而不是让消费者自己撞见 `any`。
  **已发布并追平**：`npm publish` 后 registry `latest` = 0.1.9（shasum `270a119729d3…`），`tests/kernel_fixtures/manifest.json` 升到 0.1.9，`scripts/check_kernel_fixtures.py` 对**已发布包**逐文件核过 9 份；`scripts/diff_kernel_implementations.py --kernel <0.1.9 dist>` 55 例判决、摘要与注册表语义两侧一致。


- **场景③写侧翻转：`.iterate/decision-log.jsonl` 现在装的是内核的规范条目，不是本壳的私有形状。**
  `decision_log.append_entry` 不再 `json.dumps({timestamp, round, type, data})`：它把这条记录经
  `kernel_decision_record.decision_log_draft_from_record` 转录，再用 `kernel_decision_log` 追加成带哈希链的
  条目（`sequence`/`prevEntryHash` 由链自己拥有，调用方塞不进去）。**读侧一字未改的读者**（`last_state`、
  `ci_report`、`html_report`、`replay`、`batch`、`trend_store` 与四条 web 路由）继续拿到同一个
  `DecisionLogEntry(timestamp, round, type, data)` 视图——转录规则 3 把 `data` 整份复制进 `payload`，
  所以投影是有意义的，不是靠发明字段。
  **`VALID_ENTRY_TYPES` 不再是闸门**：内核故意没有决策 kind 的封闭集（`^[a-z][a-z0-9_]{0,31}$` 是形状规则），
  因为"加一种决定就要发一次内核"正是分叉的成因。本壳那份 8 项清单与 plugin 那份 10 项清单的差
  （`round_failed`/`resume` 在这里被拒、在那里合法）就此结束——`append type=resume` 现在写得进去，
  `type="Round Failed!"` 被拒并给出 remedy。清单留作 `KNOWN_ENTRY_TYPES`（文档，非校验），旧名字保留为别名。
  **三件有意的行为改变，都不是回归**：① 全 legacy 的历史在**下一次写入时迁移**成链（逐条转录、id 的
  时间前缀取记录自己的 `createdAt`、原字节留在同目录 `.pre-ledger-*.bak`，新链先在内存里 verify 再换文件）
  ——id 若取迁移那一刻，八月的决定会排到它自己之后，而链照样 verify 得过，所以这条单独钉住；② 内核读不进
  的历史（时间戳不可解析等）**拒绝追加**而不是写过去，且一个字节都不改：静默跳过正是让账本缺口看不见的方式，
  而审计面与报告面是两个问题——`read_entries` 照旧宽容（坏行跳过、不抛）；③ 链已开始之后又出现 legacy 行
  （TypeScript plugin 至今写旧形状）＝**拒绝**，并给出可执行出路 `archive_contested_log()`（无损归档、0600、
  下一写从 sequence 0 重启）。这里不能"就地修好"：重算一条已带哈希的行就是伪造它，留着它则整本 verify 不过；
  把重启做成静默行为等于抹掉连续性，所以它必须是人说的那一句——也因此归档一个健康的账本同样被拒。
  两个 best-effort 边界（`defensive/kernel.py` 记假设、`iterate_assumption` 工具）原来只吞 `OSError`，
  现一并吞 `DECISION_LOG_REFUSALS` 并把 code 打进日志/回包——否则一条被拒的假设记录会把它本要告知的编辑
  循环打断。
  证据：`tests/test_iterate/test_decision_log.py` 13 → 21 条（迁移与原字节留存、id 的时刻来源、交织拒绝
  ＋归档往返、非交织不许归档、磁盘字节能被 `hashlib` 独立重算、目录 `0700`、kind 形状双向）；两条按新语义
  改写（旧断言要的是 `json.dumps` 冒号后的空格，以及"坏 type 就是不在 8 项里"），一条旧宽容用例
  （`corrupt entry 不影响后续追加`）换成"拒绝且零改动"。**未做完的一半点名**：`harness/iterate-plugin`
  那份 TS 仍写旧形状，而它今天完全不依赖 `iterate-kernel`（只有 js-yaml 与宿主 peer 依赖）——翻转它等于给
  一个已发布插件加一份运行时依赖，那是取号与打包的决定，不在这里替它做。

- **场景④翻转：收敛判定权从审核报告交给内核（owner 2026-10-13 的裁决落地）。**
  `loop_policy.IterateLoopPolicy._decide` 此前第一句就是 `if snapshot.converged:`——那是
  `review.build_review_report` 对"最后一轮 0 条新发现"做的算术，不是任何人对"这一轮能不能停"的
  判定。现在它把轮次喂给 `convergence_policy.convergence_decision`（内核 `iterate.convergence/0.1`
  的第二份实现，53 例两侧一致由 `scripts/diff_kernel_implementations.py` 量），只有内核说
  `halt`/`converged` 才停。**三个入参至此各有其主**：`invariant_state` 与 `evidence_complete` 由
  轮次在 `iterate/loop_gates.py` 现场量（上一批），`breaker_level` 由**循环自己**数连续无进展轮
  （0..3，3 即跳闸）——轮量不到所在跑的升级，所以 `loop_gates` 仍然不产它，理由从"本机没有断路器"
  改成"它不属于轮次这一层"。跳闸在循环侧与内核规则 1 **两处都拦**：喂不进内核的跑（缺闸）正是需要
  被拦的那个，只留内核一处就会漏。
  **两条有意的行为改变，不是回归**：① 没测不变量闸、或没做证据举证的跑**不再自动收敛**，走到 round
  cap 停，且停止理由点名原因——`round cap reached (3) — kernel not consulted: no invariant verdict
  was measured; no evidence attestation was made`；② `invariant_state == "fail"` 从"停在 gate
  failed"改成内核规则 5 的 `fix`（再多跑一轮去修），代价是一道坏掉的闸现在会花到 cap 而不是早停,
  它守的诚实性由结构保证：`converged` 这个理由只能由内核产出。
  `ReviewProgressEvent.converged` 随之改为**内核的结论**而非报告的旗标（UI/Web 上那句"已收敛"此前
  可以在循环继续时亮着）；`services/compact` 跨压缩边界给模型的那行也从裸 `converged=True` 改成
  `reviewerClaimedConverged=True` 加上实测闸位列——告诉模型一个 harness 已经不认的结论就是假指示器；
  `iterate/prompts.py` 第 4 步的指令同步改成"停不停是循环的判断"。
  证据：9 条新用例 + 2 条按新语义改写（旧的两条断的是"没闸也能收敛"，其前提已被裁决推翻）。其中
  `test_the_breaker_level_is_actually_fed_to_the_contract` 是接缝用例：两处守门结果同串，不单独钉
  "数字真喂进契约"的话，把 `breaker_level=0` 写死也没人红。变异九发各打中具名用例、前后 sha256 核回；
  **其中第三发第一版是零红**——那道缺席门与内核自身的构造校验得出同样的停止行为、不可区分，所以没有
  给它编一条"看起来能红"的断言，而是把拒绝原因写进停止理由，让它成为可观察、可红的 surface。
  整包 `pytest -q` 2715 passed / 6 skipped（基线 2702，本批 +13；本机需 `python→python3` shim），
  rebase 到 0.1.8 之后复跑 **2717 passed / 6 skipped**（多出的 2 条是契约那批自己加的 Python
  用例），ruff 0.16.8 干净。那 2 条 warning 来自本批未触碰的 `tests/test_swarm/test_imports.py` 与
  `tests/test_tools/test_task_tools.py`（一条未 await 的 mock 协程、一条子进程 transport 析构），
  不是本批引入。顺带删掉 `tests/test_services/test_compact.py:640` 一处 HEAD 上就有的未用
  import（F401）。
  **与本批同日落地的内核 `0.1.8`（`9d6d9036`，`breakerLevel` 改为可选）关系要说清**：那批把
  "缺席"与"0"在类型上分开，为的是让没有断路器的跑不必填一个主张"存在断路器且未跳闸"的 0；本批则
  让 iterate 循环**有了**断路器（数连续无进展轮），所以每次咨询都带一个真实的 0..3。两种形状在这份
  契约下都合法、主张不同，互不覆盖。
  **一条实测的红记在案，不是本批造成的**：`scripts/diff_kernel_implementations.py` 默认解析的是
  **已发布**产物 `node_modules/iterate-kernel@0.1.7`，那份按 required 拒收缺 `breakerLevel` 的
  state，于是 0.1.8 新增的 `convergence/absent-breaker-still-halts-on-budget` 判成
  `TS refused, Python accepted`。把尺子指向我按 0.1.8 源码现编的 dist 时 **55 例一致**（TS 侧
  `test/convergence.test.mjs` 实测 24 例全过）。所以这条红不是两侧实现分叉，而是 **0.1.8 尚未发布、
  harness 的 pin 未升级**；CI 不跑这把尺子（grep `.github/workflows` 无引用），上游不会替我喊红。
  发布 0.1.8 并追平 `tests/kernel_fixtures` 的 pin 属内核发布线的动作（npm 不可逆），本批不代做。
  **点名未做**：报告侧的 `converged` 字段仍是"最后一轮 0 条新发现"的算术，checkpoint / `ih iterate
  report` / html 报告读的都是它，于是它现在可能与循环结论不一致——不一致是可见的（停止理由 + compact
  那行的闸位列），但把三处命名统一是另一批的活，本轮没有顺手改 30 个文件。

### Fixed

- **`scripts/diff_kernel_implementations.py` 不再把"契约有两个版本"读成"两份实现分叉"。** 它原先按
  候选路径拿到**第一个**有 `dist/convergence.js` 的 `iterate-kernel` 就用，不核版本。10-18 实测：
  语料已随 `9d6d9036` 重钉到 0.1.8，而默认解析落到 GlassPane harness 里那份**已发布的 0.1.7**，
  于是 0.1.8 新增的 `convergence/absent-breaker-still-halts-on-budget` 报成
  `TS refused, Python accepted`。这条红又不在 CI 里（grep `.github/workflows` 无引用），下一轮跑
  尺子的人按提示去看会以为 Python 侧写错了——最省事的"修法"是把对的那一侧改弱去对齐旧产物。
  现在候选的版本必须等于 manifest 钉的版本，否则**跳过并打印具名拒绝**（`the wrong artifact, not a
  disagreement`，附两条补救：内核线发布该版本后重钉，或 `--kernel` 指到按它构建的 checkout）；
  `package.json` 读不出版本同样拒——不能假设手里那份就是钉的那份。全部候选被拒 ⇒ exit 2，与既有
  "取不到就是红、不许静默跳过"的口径一致。
  新 5 条用例（importlib 直接驱动脚本函数，`REPO`/`MANIFEST` 都 monkeypatch 到 tmp，不依赖本机
  装了什么）；变异三发各打中具名用例（接受任意构建／把钉的那份判成过期／walk 直接回第一个 dist），
  前后 sha256 核回；ruff 0.16.8 干净。实测这条红现在长成：`refused: resolved …iterate-kernel is
  iterate-kernel@0.1.7 but the vendored corpus pins 0.1.8 …`，exit=2。
  **决定记在这里**：本批**不代发布** `iterate-kernel@0.1.8`。npm 版本不可变，而 0.1.8 的 TS 与语料
  仍可能在那条线上继续动，发出去就只能越过、不能替换；pin 追平属内核发布线的动作。补救命令：
  `cd kernel && npm ci && npm run build && npm publish`，之后把 `tests/kernel_fixtures` 的语料与
  manifest 按**已发布包**逐文件重记。

### Added

- **场景②的下半场：维度归属进了台账，压缩钩子按内核的判决排队。** 内核的 `decision-log-entry`
  schema 一直允许可选的 `dimension`/`operationId`，`kernel_decision_record` 也只在它们**合法**时抄进
  draft、不合法就静默丢掉——但生产侧从来没有人把这两个键写进 `data`：`iterate_decision_log` 的 append
  只收一段自由 JSON。于是 `dimensionCoverage` 那行统计的是"代理恰好打了什么字"。
  **生产者半边**：`IterateDecisionLogInput` 多收 `dimension`/`operation_id`，校验用的就是仓库其余部分
  import 的那两件内核物件（`DIMENSION_ID_PATTERN`/`DIMENSION_ID_MAX_LENGTH` 与 `OPERATION_ID_PATTERN`），
  这里一个字面的正则或数字都没有；不合法就按工具既有的拒绝形状给出 code＋remedy，且拒绝路径上台账
  **一个字节都不多**（逐条前后 `read_bytes` 对比）——拼错的维度被转录悄悄丢掉，覆盖率会少报还看起来像量过。
  合法值合并进 `data` 后由转录自己 lift；append 成功即刷新已发布的 `iterate_state["dimension_coverage"]`，
  复用 aggregate 那一份算法。**只在快照已存在时刷新**：本工具不拥有 `iterate_state`，造一份半空的等于替
  loop_policy 编一个没人量过的轮次数与 mode。
  **消费者半边**：`_collect_compactable_tool_ids` 现在收 `project_root`，把每条 tool_result 的文本扫出
  opId（搜索形式是 import 进来的 `OPERATION_ID_PATTERN` 去锚点加词界，不重述语法），经台账映射到维度，
  顺序由 `dimension_context(planned, recorded)` 的 **status** 决定——**不比计数**，与收敛判定同一条规矩。
  verified 维度的历史排到队首先清，非 verified 的排到队尾后清，**没有归属的块保持今天的位置一动不动**
  （缺席只能读成缺席）；`keep_recent` 的含义没变，清的条数与今天逐条相等，变的只有"是哪几条"。
  规则 1 在这里有一个必须写出来的后果：**已计划且被归属的维度必然是 verified**，所以能被推到队尾的
  归属只剩"计划里没有的那个 id"，测试就是按这个形状写的。
  两份账不再各写各的：新模块 `iterate/dimension_ledger.py` 是 run-plan 文件与台账的唯一读者，工具那行
  与压缩那个顺序都从它出发（四个新公共名字已登记进 `_EXPORTS`/`__all__`）。
  **测试**：`tests/test_iterate/test_decision_attribution.py` 与
  `tests/test_services/test_compact_dimension_order.py` 共 21 例；变异七发各打中具名用例——去掉校验(3 红)、
  大小写折叠(4 红，含既有 `test_aggregate_coverage_counts_only_valid_dimensioned_entries`)、改成首次归属取胜(2 红)、
  连未归属的块一起重排(3 红)、拿计数比代替内核 status(3 红)、多清一块(预算不变性那例红)、从
  `compact_conversation` 那里漏传 `project_root`(接线那例红，证明它不是空跑)——三个改动文件
  每发之后都 `cp` 副本核回，sha256 逐字相等。`engine/query.py` 与 `/compact` 命令各加一个
  `project_root` 传参把台账的根送到压缩钩子手上；不动 `mcp/client.py`、`glasspane_reverification.py`、
  `reverify_trigger.py`，版本号一位没取。

- **场景①接线：不变量判失败的那一轮，真的去问一次引擎。** `iterate/glasspane_reverification.py`
  能发 `iterate.reverification/0.1` 也能原样转发引擎的回答，但**循环里从来没有人把请求交给它**——
  本批补的就是这一只手。落点选在轮次发布处（`iterate_review` 的 aggregate），因为只有那里同时握着
  四样输入：`loop_gates.measured_gates` 刚**量过**的 invariant 判决、该判决背后的那份 invariant 报告
  （请求的 `trigger.reportDigest` 要引它）、本轮即将发布的 `iterate_state`、以及台账的根。
  `loop_policy` 只能在轮边界看到已经转发过的判决字符串、看不到报告，在那里建请求等于替它造一份
  报告；`engine/query.py` 则刻意不把 defensive kernel 带回 tool_metadata（`DEFENSIVE_KERNEL_KEY`
  在合并时被跳过），所以它也拿不到报告。绑定规则全部是既有规则，写在新模块的 docstring 里：
  **没有判决就一个字都不说**（返回 `None`，调用方什么都不发布——"不需要"与"不知道"必须可读出来）；
  **判定不是我们的**（只经 `any_verified`/`all_clean` 转发，不比计数、不改判）；
  **重验绝不把闸变成已测**（不写任何 gate key，引擎不可达/拒绝时闸与 `loop_gates` 产出的一字不差）；
  **拒绝原样转发**；**传输由宿主给**——本仓自己的 `McpClientManager.call_tool` 是 async，而
  `run_reverification` 是同步发送方、遇到 awaitable 回 `GP_E_ASYNC_HOST` 而不是把协程当回答归档，
  所以宿主没在 `glasspane_reverify_transport` 交出同步只读传输时，这一轮**记下"没发送"**（这是状态，
  不是沉默）。`defensive/kernel.py` 为此补了三个只读属性（`last_invariant_report`/`invariant_ensure`/
  `invariant_commands`）：没有它们，真内核下这道闸根本读不到、摘要也建不出来。
  新增 `tests/test_iterate/test_reverify_wiring.py` 12 条（真 `DefensiveKernel` 跑出来的报告 +
  语料证据包 + 钉死的 `iterate.reverification/0.1` 词表）。
  主代理独立验因果：把"没有判决"那条 `return None` 改成返回一个空 payload，
  `test_no_verdict_sends_nothing_and_claims_nothing` **当场具名变红**，按 sha256 `cdac151f…` 核回后 12/12。
  **仍未通的一半**：宿主侧的同步传输没人提供，所以这条通路今天在生产里是"记为未发送"；而
  `mcp/client.py` 把多 item 回包 `"\n"` 拼接的做法会把一份真 `gp_diagnose` 帧读成 `unparseable`
  （本轮实测，见 CHANGELOG 顶部那条缺陷记录）。这两件不通，场景① 只能算接好了一半。
- **场景⑤接线：两个判定生产者真的注册，消费它们的是拒绝分支而不是装饰。** `iterate/provider_registry.py`
  此前只有尺子读它（`scripts/diff_kernel_implementations.py` 与自身测试）。新增
  `iterate/provider_producers.py` 以夹具 `registry.iterate` 那两条原样的 id/kind/method/requires 注册
  `iterate-engine-ae`（`diagnose` requires `evidence-pack`）与 `iterate-report-render`
  （`render-markdown` requires `decision-log`），**version 取 `iterate_harness.__version__` 现读**、
  不抄夹具的 `0.1.4`（夹具注释自己说那是"写作当时的包/引擎版本"，抄它等于替产品认证一条产品没测过的主张），
  `requires` 的词表由引擎包自己的 camelCase 字段名派生（`circuitBreaker`→`circuit-breaker`），不重打字面量。
  两处消费都是**能改输出的**：① `glasspane_reverification._relay_pack` 先问契约"这份包可以读判定吗"——
  信号字段全在而全空的包不再进转录（那会回一个把"没测"读成"引擎看不出来"的 `inconclusive`），
  改由提供方的 `PROVIDER_E_METHOD` 拒绝并按引擎拒绝的原样转发 code/message/remedy，答上的那些在
  `RelayedAnswer.method` 里带出它是按哪个方法读的（出处，不是判决）；② `iterate report --pr` 在空台账上
  原先印 "No findings recorded."（没测的运行读起来像干净的一次），现在由注册的
  `report-render` 拒绝，因为它声明 `requires: ["decision-log"]`。
  具名用例 25 条（含版本变异对照、重复注册拒绝且注册表不变、`[]` 与缺 `requires` 是两个主张、
  `ids_in_common_with` 对出厂两份回 `[]`、对夹具 `collision` 段回那两个 id），语料清单里点名这两个新消费者。

- **场景②接线：运行计划真的发布，压缩账里带上内核的维度判决。** `iterate/run_plan.py` 与
  `iterate/dimension_context.py` 此前在 `src/` 里零消费者（只有各自的测试与
  `scripts/check_run_plan_channel.py` 用它们——2.7.x 那条更正至今成立），本批把它们放进循环：`_plan`
  用**原始 config 映射**（`load_config`，不是 effective config——发布的必须是人写下的维度表，不是本壳
  替没声明的项目发明的缺省值）建并写 `.iterate/run-plan.json`，回 `{path, digest, dimensions}`，并追加
  一条 `decision` 台账把 digest 落进哈希链；`_aggregate` 把
  `format_dimension_context(dimension_context(planned, recorded))` 压进 `iterate_state`，经
  `AggregateSnapshot` 透传，由压缩附件打印成 `- dimensionCoverage: …`，**与私有 `byDimension` 并存不取代**
  （后者说发现出自哪一维，前者说计划里哪几维一条决策都没有），缺席则整行不印——印一个发明的 0/0 等于
  对压缩后的模型声称一条本壳没有的事实。recorded 只认台账 `data` 里合法的 `dimension`，没有该键的条目
  不属于任何一维、不猜。取号留给发布线：新 payload 字段与新打印行都是用户可见形状。
  两半未做，照实记：microcompact 的"优先压缩无关维度历史"没动（`ToolResultBlock` 只有
  `tool_use_id/content/is_error`，与台账的 `op_<ULID>` 之间没有 join 键；可行路子是从内容里捞 `op_` 号，
  本轮不做假样子）；`iterate_decision_log` 的 `data` 仍是自由形状，所以账里有没有维度取决于写的人是否
  声明，把它做成显式参数是一次取号决定。

- **场景④那两把闸的对照补到能红（只动测试，未动生产代码）。** 上一批的
  `test_breaker_level_is_never_invented` 断的是 `"breaker_level" not in GATE_KEYS`，而
  `GATE_KEYS` 这个常量在生产代码里**没有任何读者**（grep 实测只命中声明与这条测试）——于是它测的是
  一张清单而不是行为。本批发了一版对照证据：把 `gates["breaker_level"] = 0` 真的打进
  `measured_gates()` 后，按原式子重放那条断言**仍然绿**，而新断言（五种输入形状逐个跑
  `measured_gates()`，断返回的键 ⊆ `GATE_KEYS` 且永不含 `breaker_level`）当场点名变红。
  另补 `8664b0e` 点名的端到端欠账：aggregate 发布那行原先只有单元级覆盖，现在 5 条工具级用例钉住
  真锚点⇒`True`、编造路径⇒`False`、通道关⇒键缺席、kernel 实例进 metadata⇒`invariant_state=pass`、
  没 kernel⇒缺席。
  变异五发（打 0 档 breaker / 硬编码 True / 无视通道开关 / 摘掉 invariant / 停止发布）各打中具名
  用例，变异前后按 sha256 核回。`tests/test_iterate` 1065→1070，整包 `pytest -q` 2702 passed /
  6 skipped，ruff 干净。
  **两条如实记在案、本批不做的**：① `loop_gates.py:22` 的 `from .types import Finding` 是悬空引用
  （真名 `ReviewFinding`，mypy 实测 `attr-defined`），origin/main 上仍在、但共享工作树里已有一条
  **未提交**的并行编辑把它改掉——那是别人的批次，我不抄不覆盖；② `AggregateSnapshot` 仍不带
  `evidence_complete`，因为此刻没有任何读者会读它，等④翻转那批把字段和用法一起加（先加就是没人读的
  计数）。
- **`iterate-kernel@0.1.8`：`RoundState.breakerLevel` 改为可选，缺席不等于 0（场景④最后一闸）。**
  契约原文说 `breakerLevel` 是"引擎自己为这次运行报的等级"，可 iterate 循环里根本没有这个值：
  实测 harness 只在按需复核（`gp_last_evidence`）时取证据包，没有任何地方把某个包缓存在轮次
  metadata 里，而本机也没有断路器概念。此前 producers 只有两种选择，都不诚实：要么不发状态，
  要么填 0——而 0 断言的是"存在断路器且未跳闸"。现在缺席合法，规则 1 显式跳过；TS/JSON Schema/
  Python 三处同一批改，缺席与 `0..3` 在类型上就分得开。
  对照是拿**真发布物**取的，不是自说自话：同一份 `{round:2, maxRounds:7, invariantState:pass,
  openFindings:0, evidenceComplete:true}` 在 0.1.7 上被 `KernelSchemaError` 拒在
  `breakerLevel`，在 0.1.8 上判 `converged / all-gates-green`；再加一条 `absent-breaker-still-halts-on-budget`
  钉住"跳过规则 1 不等于放水"——预算到了照样 halt。语料两例 + manifest 升到 0.1.8（9 条
  sha 全重钉）；内核 231 条、Python 收敛与语料守卫 40 条、跨语言当面比对全一致，ruff 干净。


- **`iterate/loop_gates.py`：轮次边界只报"真的量过的闸"（场景④后半：`evidence_complete`）。**
  聚合发布点此前只有 `invariant_state` 一条；`evidence_complete` 的真值在**另一个 action**
  （`_meta_review`，`iterate_tools.py:391`）里算，而循环是在轮次边界做判断的——拿一个可能还没
  发生的调用的结论当测量不行，所以在发布点用同一个纯校验自己量一遍。选择逻辑收进一个函数，
  工具侧只剩一行 `state.update(...)`，没有端到端用例的表面积就只有赋值那一行。
  `breaker_level` 明确**不产**：本机没有断路器，填 0 与"有断路器且报告未跳闸"不可区分——
  所以宁可让调用方继续拒绝咨询内核，也不喂它一个没人拥有的数字（用例
  `test_breaker_level_is_never_invented` 把这条钉住）。另钉两条容易糊过去的：证据校验关闭时
  键必须缺席（不得默认 True）；findings 为空是"无可验证＝完整"，那不是"项目已验证"——
  后者由 `open_findings` 说。5 条新用例；`test_iterate_tools` + 循环策略 + 两份内核套件全绿，
  ruff 干净。判定权仍归 `loop_policy`，本批不改任何收敛行为；翻转要等 `breaker_level` 定源。


- **`iterate/invariant_state.py`：收敛契约那个"没人看过就别装成看过"的字段，终于有了唯一
  合法的来源（场景④的前半）。** 内核的 `RoundState.invariant_state` 取
  `pass|fail|error|unmeasured`，而规则把差别全押在这个档位上：闸门没跑过就不能当跑过。此前
  harness 里没有任何地方能给出这个值——不变量检查只在 code mode 的 `DefensiveKernel` 里跑
  （`engine/query.py:262` 的 `_new_defensive_kernel` 在非 code mode 直接返回 None），而
  `DEFENSIVE_KERNEL_KEY` 其实早已被 `engine/query.py:1464` 放进 exec metadata，工具层拿得到，
  缺的只是那一次映射。本批把这一步做成一个模块：`checks_run<=0` 一律 `unmeasured`、
  `kind=="refused"` 记 `error`（那是检查自己跑不起来，不是项目违例）、否则按 `passed` 给
  `pass`/`fail`；**没有报告就返回 None**，调用方把键整个省掉——填 `pass` 是让没测过的跑凭一个
  没人生产的结论收敛，填 `unmeasured` 也是一种主张。
  第一版就藏着一个假绿，是探针抓出来的：`InvariantReport()` 的 dataclass 默认 `passed=True`，
  旧映射在 `checks_run=0` 时穿过 `violations` 判空落回 `pass`——即"空报告"读成"闸是绿的"。
  现在这条被写成用例钉住（`test_an_untouched_report_is_unmeasured_not_pass`）。读侧同理只收
  四个合法值，拼错的字符串按缺席处理（`test_the_loop_state_never_invents_a_value`）。
  `AggregateSnapshot.invariant_state` + `iterate_tools` 聚合发布各加一处，值缺席时不写键。
  新 7 条用例；`test_iterate_tools`/循环策略/内核三套共 158 条全绿，ruff 干净。
  **本批没做完、点名的**：聚合工具那行发布没有端到端用例（工具测试里没有 code-mode 的 kernel
  实例），要等④翻转那批一起补；`breaker_level` 与 `evidence_complete` 两个入参仍无来源；
  判定权仍归 `loop_policy` 自己——没改任何收敛行为。

### Fixed

- **`scripts/install.py::_installed_version()` 在 Python 3.10 上不再报假版本。** 包里
  `requires-python >= 3.10`，而 `tomllib` 是 3.11+ 才有——缺模块的 `ImportError` 被
  `except Exception` 与"清单坏了"同一路径吞掉，于是在一个受支持的解释器上
  `iterate-installer --version` 打印 `0.0.0+unknown`，而 `pyproject.toml` 写的是 3.5.0。
  修法是把两件事分开：`ImportError` 走无解析器的行读取（只认 `[project]` 表里的
  `version = "…"`，遇到下一个表头即停），真·TOML 语法坏仍回落到占位符。
  同时改掉那条让缺陷看不见的测试：`tests/test_install_script.py` 原先无条件
  `import tomllib` 来算期望值，等于"测试只在产品能跑的解释器上跑"——3.10 的 CI 因此
  连着红了 5 个 run（`c8f2e14`/`13187a6`/`ff249de`/`37b480d`/`59307ad`），把这期间每一批的
  验收信号一起盖掉。现在期望值由测试自己按行扫读取，另加三条：模拟 `tomllib` 缺失
  （任何解释器都能测到 3.10 那条路）、别的表里的 `version =` 不得被当成版本、
  读不出来时返回 `None` 而不是猜。
  实测：3.11 上该组 6 passed；真 python3.10 下 `_installed_version()` 从
  `0.0.0+unknown` 变 `3.5.0`；反向对照（把新回退撤掉）当场回到 `0.0.0+unknown`，
  证明这条分支承重而非装饰；改完按 sha256 核回，ruff 干净。

### Added

- **`iterate-kernel@0.1.6`：决策日志条目学会说"哪一种决定、第几轮、哪个维度、凭什么"，
  并新增 `decision-log-transcribe` 把两侧**已经在写**的日志映射成这个形状（场景③的
  契约半边）。** 条目此前只有 `{entryId, sequence, prevEntryHash, summary, outcome,
  createdAt}`——够 GlassPane 记工具执行，但记不下 iterate 循环写的
  `{timestamp, round, type, data}`：实测两侧词汇表已经在分叉（plugin 的 TS 认 10 种
  `type`，Python 的 `VALID_ENTRY_TYPES` 只有 8 种，`round_failed`/`resume` 一进 Python
  侧就被当成非法）。四个字段全部可选：省略即不进规范字节，因此**既有链节的摘要不变**，
  这不只是"能过解析"——对照用例把"不带新字段的落盘行里不得出现 `null`"钉成具名断言，
  并反向变异证明它咬得住（把"缺席"写成 `null` 时 15 条链测试同时变红）。`kind` 故意
  **不做枚举**：两套词汇各归各家，内核只保证可比（形状/边界），否则每加一种决定都要发
  一次内核版本——和 `dimension-context` 不持有维度清单同一个理由。`dimension` 复用
  `DIMENSION_ID_PATTERN`（导入而非重述），这是"recorded 半边"能从任一生态的账本里被
  覆盖率算术看见的前提。`payload` 有 8192 字符的规范 JSON 上限，且这条上限**JSON Schema
  表达不出来**——按既有纪律写成具名分歧用例（ajv 放行、zod 拒绝），而不是默默当约束存在。
  `decision-log-transcribe` 的十条规则连同 13 个用例落在
  `kernel/fixtures/decision-record-transcription.ok-01.json`，Python 半边照这份档案实现，
  不读 TypeScript。其中一条是测量逼出来的：`Date.parse("11/10/2026 02:28")` 在 V8 里会按
  美式月/日猜出一个时刻，而 Python 直接拒绝——所以时间戳改由显式时区的 ISO-8601 模式先把关，
  否则"同一条记录被谁转换就归谁解释"，账本不再是证据。225 条内核测试全绿（本批 +26），
  `npm pack --dry-run` 实测 92 文件、新模块与新档案在包内。
  **未随本批落地（下一批）**：Swift `DecisionLogEntry` 绑定还没加这四个可选字段——它解码时
  会静默丢弃它们，重编码就丢审计信息；GlassPane 侧的 fork 重钉 0.1.6 与 kernel-conformance
  的新 oracle 同属那一批。
- **`iterate/kernel_decision_record.py`：转录层的 Python 半边，与 0.1.6 的 TS 实现当面比对
  （场景③的对称半边）。** 第二份实现照
  `tests/kernel_fixtures/decision-record-transcription.ok-01.json` 的 `comment` 写，不照
  TypeScript 写——这才是"两套实现一致＝契约拿到证据"，而不是"抄了一份镜像"。三件照抄照不出
  来的事：① 时间戳自己按"ISO-8601 带显式时区"的模式把关、日历运算手写截断，不用
  `fromisoformat`——3.10 只接受 3 位或 6 位小数秒而 3.11+ 接受任意位，且
  `Date.parse("11/10/2026 02:28")` 在 V8 里会猜成美式月/日、Python 却直接拒绝，两边必须按
  同一条规则拒绝而不是各按各的解析器；② 可选字段缺席就是不写键，绝不写 `None`（链的摘要盖在
  规范字节上，把"缺席"写成 null 会让每条既有链节换摘要）；③ `outcome` 只读不猜，读不到就是
  `inconclusive`。
  语料位点：manifest 升到 0.1.6，新增两条 sha256 钉（转录档案 + 扩展条目档案），
  `test_kernel_fixtures.py` 的四条守卫（vendored==pin、消费测试存在、实时 kernel 检出与 pin
  一致、篡改被拒）实测仍绿。18 条新单测；跨语言对照 `diff_kernel_implementations.py` 加进第
  四个模块后实测 53 个用例判定一致（原 40 + 转录 13），并反向变异证明它咬得住——把 Python 侧
  "没记到"改成"pass"时点名红在 `transcribe/silent-record-stays-inconclusive`。
  本批纯增量：`decision_log.py` 的写侧还没翻成内核形状（那一步必须同批改读侧
  `last_state.py`、WebUI 与 plugin 的 TS 副本，另批做）。

- **`iterate/glasspane_reverification.py` + `scripts/check_glasspane_reverification.py`：
  场景①的发送线，实机跑通。** 内核造出 `iterate.reverification/0.1` 之后，本模块负责把它
  **发出去**并原样转述引擎答案：operationIds → `gp_last_evidence` + `gp_diagnose`，其余
  一律不排。三条边界各是这条链的要点：① 绝不发变更型调用（`gp_act`/`gp_snapshot`/
  `gp_restore`/`gp_attach`/`gp_export_evidence` 在名单里点名排除），重跑配方步骤是对用户
  机器的决定，属于宿主权限面，不属于错误路径；② 没有通道就说 `NOT attempted`，不返回空集
  让人读成"没意见"；③ 引擎的拒绝原样转述（连 remedy），不重新编码、不改写。
  实机证明（`glasspane-mcp` 1.6.2 + 钉住的 `mcp` 2.0.0，16 工具在列）：真实
  `check_invariants` 跑出 outcome=fail → 真请求
  `iv_15d87c73d4ef809ec…` → 引擎回 `outcome=inconclusive`（attribution soft）与一条
  `GP_E_NOT_ATTACHED: no app attached | remedy: call attach first`，2/2 转述成功。
  缺字段的包（只有 `schemaVersion`）以前会被转成 `inconclusive` 结论——那是"没测到"不是
  引擎判断，现在点名 `GP_E_PACK_INCOMPLETE` 拒绝转述。同步 runner 撞上 async 宿主时，
  协程不再被当成答案，而是点名 `GP_E_ASYNC_HOST`（这条是它自己暴露的真 bug）。
  17 条单测（真档案来自已发布语料、拒绝行来自实机捕获）；反向变异五刀各打中一条具名
  用例：撤 async 守卫、把配方步骤排成 `gp_act`、撤包完整性闸、不解析 shell 的拒绝行、
  无通道当跑过。
- **`check_glasspane_channel.py`：把"没连上"与"不广告工具"分开说。** 之前它只读
  `list_tools()`，一个连接失败的 server 会被报成"GlassPane 不提供 gp_*"——那是对产品的
  一句假话。现在先读 `list_statuses()`，失败就写 `NOT measured` 并附 detail；detail 长得
  像字段改名时，再报出 SDK pin 事实（本机 `mcp` 1.29.0，pyproject 钉 2.0.0；
  `tool.input_schema` 只在 2.0.0 存在）。这台机器上 harness 的 MCP 层当前连不上任何
  server，与本批无关（同一状态在我这批改动的"对照前"复跑里同样复现）。

- **`scripts/check_run_plan_channel.py`：把场景②的共享通道真跑一遍，而不是各自自证。**
  本仓真生产者（`build_run_plan` + `write_run_plan`）用 GlassPane 自己的
  `iterate.config.yaml`（`gui` scope）造一份计划，交给 fork 的
  `readRunPlan()`（bun 直接 import 产品里那份 `kernel.ts`，不是抄本）读回来，比
  id 序列与 `rp_` 摘要。实测一致：两侧都是 `rp_25c3aeece0fa54293448fe0a1525f45a24f01ae797a766a23c54911bf5a5c2fe`，9 个 id 顺序一致。
  两道红是**跑出来的**，不是推理的：`GP_CHANNEL_SELFTEST=corrupt`（把维度数组重排——
  字节仍然合法，schema 看不见）→ exit 1；`GP_CHANNEL_SELFTEST=mismatch`（另一侧回同一
  份字节但少报一个 id）→ exit 1。第二条是关键：只比摘要的话这个分歧会被盖住，所以摘要
  之外还比 id。没有 bun／没有 harness／配置读不出来 → exit 2 明说"没测到"，不允许
  静默通过。
  写的过程中修掉两处自己的错：`load_config` 收的是项目**根目录**而不是文件路径，
  传错会按 API 返回 None、进而伪装成生产者的 AttributeError；`mismatch` 那条对照的守卫
  写成 `and not their_ids`，那侧非空所以分支从没执行——"对照自己没跑"这件事是先被
  rc=0 暴露的，不是被读代码读出来的。

- **`iterate/convergence_policy.py`：收敛规则的 Python 半边。** kernel 在 0.1.5 里把
  「一轮跑完下一步是什么」定成了契约（熔断 > 轮次预算 > error > findings > 证据不全 >
  未测量 > converged），本模块是对着已发布语料 `convergence.ok-01.json` 的 `comment`
  写的第二份实现——不是把 TypeScript 读一遍再翻，而是照规则文本反推，期望值一律从语料文件
  取。关键不对称保住：`unmeasured` 与证据不全永远是 `verify`，不得读成 `converged`
  （"看不了"与"看着没事"是两回事）。15 条语料用例逐条比对，另加"每一条 precedence 都
  必须打得过它下边每一条"的表驱动测试；`scripts/diff_kernel_implementations.py` 实测
  15/15 与 TS dist 判定一致。
- **`iterate/invariant_report.py` + `iterate/canonical_json.py`：场景①的生产者半边。**
  `defensive/invariants.py` 的真实 `check_invariants` 只记违规和一个计数，不记通过项的
  名字——直接照抄会得到一份"全是失败"的报告，消费者分不清"4 项里 3 项通过"和"4 项全挂"。
  本模块的检查清单来自**配置**（`invariants.ensure` + `invariants.commands`，按书写顺序），
  ok 由runner 是否点名该 label 决定，`refused` 这类保留原 kind；`outcome` 无法被合成成
  `pass`（pass 只在无失败项的分支可达，而 runner 点名的违规一定会留在清单里）。dry-run
  里被预览但没执行的命令报 `ok=false` + `not measured:` 且整体 `error`——error 不产生复核
  请求，未测不得洗成活动。`canonical_json.py` 是与 kernel/Swift 同规则的第二份规范化实现。
  实测：对本仓 kernel 目录跑一次真实 invariant（含 `refused: unsafe command` 那条真文案），
  Python 与 TS 对同一份报告算出同一个 `iv_` 摘要。
- **`scripts/diff_kernel_implementations.py`：两侧实现在同一份语料上当面比对。** 语料 pin
  和各自单测只保证"没偏离写下来的用例"，而真正会出事的往往是没人写进用例的那条分支。本脚本
  把 convergence / invariant-report / provider-registration 三份语料同时喂给本仓 Python 与
  node 加载的 TS dist，比 verdict、摘要与注册表语义；node 或已构建 dist 不在场时 exit 2 明说
  "没测到"，不允许静默跳过。实测三份全量一致。
- **`iterate/provider_registry.py`：provider 注册的 Python 半边。** 重复 id 拒绝覆盖（不是
  后写赢）、解析不猜（没有默认、没有模糊匹配、没有"只注册了一个就算它"）、要方法时点名它
  到底提供了什么；注册表每次整体替换而非原地改，避免"加进一张表、下一张表没有"的半份注册。
  两侧拒绝码按语言各自命名（`KERNEL_E_PROVIDER_DUPLICATE` / `PROVIDER_E_DUPLICATE`），必须
  一致的是"拒了没拒"和注册表有没有被改动——`diff_kernel_implementations.py` 就按这个比。
- **语料 pin 升到 `iterate-kernel@0.1.5`**，新增上述三份 fixture 并 pin sha256/字节数；
  `run-plan.ok-01.json` 的 sha 变了但契约语义没变——kernel 那边只是把 label 的出处改口
  （配置里根本没有 label 词汇）。`tests/kernel_fixtures/manifest.json` 的 consumedBy
  规则把"语料进来了但没人读"判成死契约数据，这次三条新条目各自的消费者都在上面两条里。
- **`iterate/kernel_decision_log.py`：哈希链的 Python 半边（场景③写侧翻转的能力前提）。**
  锚点是新 vendored 的 `tests/kernel_fixtures/decision-log-chain.ok-01.json`：TS 侧用
  openssl/shasum 重算、Python 侧用 hashlib 重算，两边都从**同一份落盘字节**出发——一条只有一种
  语言能重算的链是主张而非证据，这正是该档案 comment 里写下的纪律。随链一起移植的还有
  `verify / read / head / build / append / new_decision_entry_id` 与全套拒绝分支：路径必须绝对、
  目录不得可被 group/other 写、文件 0600、写后回读再验链、`sequence`/`prevEntryHash` 由账本拥有
  所以调用方发进来一律拒绝（伪造连续性是账本最不能允许的事故）。可选字段缺席一律不写 `null`，
  否则每条既有链节的摘要都会换。
  12 条用例，两处反向变异各打中一条具名断言（撤规范字节比较→红在
  `test_a_non_canonical_storage_form_is_caught_not_rehashed`；撤目录权限守卫→红在
  `test_a_group_writable_directory_is_refused`）。其中一条负例起初是**假绿**：
  `mkdir(mode=0o777)` 被 umask 掩掉，守卫根本看不见松目录；改成显式 `chmod` 之后它才真的能红。
  **本批纯增量，没动旧日志的生产者**：`decision_log.py` 仍照旧写 `{timestamp, round, type, data}`。
  写侧翻转要同批改 `last_state`/`ci_report`/`html_report`/`replay` 与四个 web 路由（实测 8 个
  读者），并用 `is_legacy_decision_record` 继续读历史行——那是单独一批协调改动；先把"能不能验"
  落地，翻转时才只改"写什么"。
  全量读数：2399 passed / 5 failed / 6 skipped，另有 `tests/test_mcp` 一处收集错误（本机 `mcp`
  不是钉住的 2.0.0，缺 `mcp.server.mcpserver`）。这 5 条与收集错误**把本批两个新文件从树上摘掉后
  逐条复跑仍然全红**（按 sha 核回），所以是既有环境性失败而非本批引入；点名留档，不当成已修。

### Fixed

- **`iterate-kernel@0.1.7`：发布出去的 `selector` 契约不再承诺"空 role 可以"。** 这不是预防性
  收紧，是被别人的闸抓出来的真缺陷：GlassPane 的 `mcp-shell/test/consumer-consistency.test.mjs`
  有一条"daemon 判据 / 已发布 JSON Schema / 内核 zod 三处必须同规则"的闸，把根仓镜像语料按已
  发布包刷新后它当场变红——`$defs.selector.role` 与 `.title` 在 canonical 侧只有 `maxLength`、
  没有 `minLength`，zod 也只有 `.max(512)`；而引擎的 AX 匹配把空串读成"没有声明约束"，空 role
  不是更窄的选择器，是**停止命名它指向的东西**，daemon 直接拒。契约在这里对 agent 许的诺和执行
  它的规则是两个答案。修法落在同一个提交的两侧：zod `.min(1)`、schema `minLength: 1`；
  `identifier` 保持无人裁定（daemon 对它没有守卫，内核也不替它发明判据，测试单独钉住这一点，
  将来若加守卫必须同批改那条用例，而不是默默继续放行）。
  内核加 4 条：role 空串两侧拒、title 空串两侧拒、identifier 空串两侧收、"schema 写明的下限必须
  等于 zod 执行的下限"；两处反向变异各自变红（撤 zod `.min(1)` 只红 zod 侧那条；撤 schema
  `minLength` 同时红掉跨绑定那条），两侧都咬得住。229 条内核测试全绿（本批 +4）。
  harness 语料 manifest 版本随升到 0.1.7（档案字节未变，evidence-pack 的 5 份 ok-* 一字未动）。
  Python 侧无需跟改：实测 iterate-harness 没有 selector 校验，命中的 `role` 全是聊天消息字段。

- **`iterate/run_plan.py`：把 `iterate.config.yaml` 变成共享内核的 RunPlan 契约。** 维度
  覆盖那套算法一直拒绝自己发明计划（是对的），而计划此前只活在这份配置里——GlassPane 侧
  读不到，于是它的会话压缩永远渲染成"0/N verified"。本模块产出计划（scope 并集按人工书写
  顺序、focus 取第一个声明它的 scope，两个 scope 各说一套时不拼成第三条没人写过的指令）、
  算摘要（`rp_` + sha256，与 kernel/TS 同一规则的第二份实现，逐字对着已发布 fixture 的
  `expectedDigest` 断言）、写文件（0600、拒相对路径、拒写引擎状态根、目录建不出来就报错，
  且先验摘要再落盘——算不出的计划不会留下半份文件）。语料 pin 升到
  `iterate-kernel@0.1.4`，新增 `run-plan.ok-01.json`。14 条测试、ruff 0.16.8、
  mypy --strict 全过。
- **`iterate/evidence_transcription.py`**：kernel 转录契约（evidence 包 → 决策日志的
  `outcome` 与 `summary`）在本仓的第二份实现。规则不是从 TypeScript 读的，而是从发布的
  语料 `tests/kernel_fixtures/evidence-decision.ok-01.json` 读的——那条 fixture 自带规则
  说明与 8 个用例（4 个真档案 + 4 个单字段派生），配套
  `tests/test_iterate/test_evidence_transcription.py` 逐条比对；两侧任一实现改了映射而
  另一侧没跟，就有一边 CI 变红。语料 pin 因此升到 `iterate-kernel@0.1.3`（0.1.2 里没有
  这条 fixture），`scripts/check_kernel_fixtures.py` 的 registry 模式实测对着线上包过。
- **`scripts/check_glasspane_channel.py`**：实测本机的 `glasspane-mcp` 能否被本 harness
  的 `McpClientManager` 连上、8 个必用 `gp_*` 工具是否在列表里、`gp_last_evidence` 回的
  证据包是否带着消费者要转录的四个字段。操作者命令，不是 pytest——产品不在就 exit 2
  明说"没测到"，不允许静默跳过。实测通过：16 工具可见，两个只读调用回真数据。

### Fixed

- **跨语言对照测试不再恒 skip**（`tests/test_iterate/test_dimension_context.py`）：
  `test_matches_the_kernel_fixture` 此前只在"碰巧有 kernel checkout 的机器"上找
  fixture，找不到即 skip——实测 `19 passed, 1 skipped`，本仓唯一能证明 Python 半区
  与 kernel TS 半区算的是同一件事的测试从来没跑过。现改为语料进仓
  （`tests/kernel_fixtures/`，按 sha256 定版）、找不到即红。
- **新增 `tests/test_iterate/test_kernel_fixtures.py`**：pin 与字节一致、每个条目必须
  点名真实存在的消费者（不许有没人断言的契约数据）、`KERNEL_FIXTURES_DIR` 指向活
  kernel 时两边字节必须一致；含负例（篡改一个字节、删一个文件、塞一个未定版文件都验过会红）。
- **新增 `scripts/check_kernel_fixtures.py`**：`--source kernel/fixtures` 对着源码树查
  （monorepo CI 用，零网络），默认模式对着 registry 发布的 `iterate-kernel@<pin>` 拉
  tarball 查（split 仓的 CI 用）。
- **更正 2.5.0 那条 `dimension_context` 的描述**：写的是"注入给评审与协调链路"，实测
  该模块除自身与其测试外无任何 import，生产链路里没有它。接线是后续条目，不是既成事实。

## [2.7.0] - 2026-10-17

整轮「代码审查 → 修复 → UX 评审 → 缺口补齐」的结果：约 40 处修复横跨权限 / 引擎 / hooks /
防御 / 任务 / 工具 / CLI / Web / cron / 配置十个面，另有若干「按钮/旗标说了但没做」的真实缺口。

### Fixed

- **`send_message` 发给刚完成的 agent 会被静默吞掉**（`tasks/manager.py`）：进程在
  「存活检查 → `stdin.drain()`」窗口内退出时，写入落进将死的管道——工具返回成功、消息却
  从未被读到（实测 10 次复现 2 次，慢机可达 50%）。现写入后既做同步退出复核，又启动
  **投递监视器**：进程在窗口内退出且**没有产生任何新输出**即视为未消费，重启任务并重投递；
  有新输出则说明已消费，不重复投递。停止/换代/驱逐会取消监视器，`killed` 任务永不复活。
- **任务 watcher 被孙进程持有的 stdout 永久挂住**：`nohup server &` 之类会让 `_copy_output`
  等不到 EOF，记录永远 `running`、完成监听器不触发。现 drain 限时 2s，超时取消后按退出码
  定终态。
- **`bash` 超时后仍可能挂数小时**：残留孙进程持续吐字节时，旧的「每次读 2s」超时会被每个
  chunk 重置。现改为整段 drain 一个总预算（2s），到点即停。
- **权限硬边界可被 `bash` 绕开**（`permissions/checker.py` + `engine/query.py`）：`bash`
  没有 `file_path` 字段，`cat ~/.ssh/id_rsa` 在 `allowed_tools: [bash]` / `full_auto` 下
  一路放行。现从命令串提取路径 token（`~`/绝对/相对路径，`shlex` 分词 + 环境变量展开），
  与文件工具一样过敏感路径 / deny 规则 / 风险区三道边界；`glob` 的 `pattern` 字段也进入
  路径提取（此前 `glob /home/u/.ssh/*` 完全绕过）。
- **`denied_commands` 只是「首命令」边界**：`cd /tmp && rm -rf build`、`true; rm -rf /`、
  `bash -c 'rm -rf /'`、行尾注释都能绕过 `rm -rf *`。现按 shell 分隔符（`&&`/`||`/`;`/`|`/
  换行）切分并对每段匹配（含递归解包 `bash -c`/`sh -c`），同时移除注释。
- **`path_rules` 的 `allow: true` 是死配置**：解析后从未参与决策（默认模式仍弹确认）。
  现作为显式路径允许（排在敏感/deny/风险区/denied_commands 之后，绝不放宽硬边界）。
- **notification hook 的 `permission_prompt` matcher 永远不命中**：payload 同时带
  `tool_name`，旧的 subject 优先取工具名。现对 notification 事件优先取
  `notification_type`；回归用例改用生产 payload 形状。
- **command hook 超时只杀 shell**：hook 子进程未独立进程组，`curl`/watcher 等孙进程超时后
  继续跑；取消时甚至完全不杀。现 `start_new_session=True` + `killpg`，并补
  `CancelledError` 清理。
- **取消 / provider 异常后历史里留下未配对 `tool_use`**：Esc 打断工具轮后
  `query_engine` 只处理 `MaxTurnsExceeded`，下一条消息被 provider 以
  “tool_use ids found without tool_result” 拒绝，会话卡死。现对 `Exception`/
  `CancelledError` 同步并为未完成的调用补 error `tool_result`（`GeneratorExit` 不处理，
  避免持久化撕裂状态）。
- **STOP hook 的 `blocked` 被丢弃**：其他事件都会尊重聚合结果，唯独 STOP 只执行不看。
  现按 reason 追加一条 user 消息继续循环，最多 3 次（防死循环）。
- **被钩子拒绝的 prompt 仍写入 task focus**：`USER_PROMPT_SUBMIT` blocked 之前就
  `remember_user_goal` 并消费了 `_suppress_next_user_goal`。现只对接受的 prompt 记录。
- **防御内核不变量检查抛异常时不回滚**：`after_mutation` 只守 `CancelledError`，检查器
  （EMFILE/OSError 等）异常会让编辑留在磁盘、快照泄漏，同时工具被告知失败。现异常路径
  回滚并返回明确错误。
- **同一文件的两个并发编辑：兄弟回滚会撤销已提交的编辑**：A commit 后 B rollback 恢复到
  共享的原始快照，A 的成功结果被静默回滚。现每次 commit 记录「最后接受内容」，兄弟回滚
  恢复到该内容；首个未提交的回滚仍恢复原始快照（新增两个回归用例）。
- **空不变量清单 = 假通过**：`invariants: {commands: {core: []}}` 会跑 0 个检查却
  `passed=True`，每次编辑都绿灯。现解析时过滤空清单，且 `checks_run == 0` 一律 fail-closed
  回滚。
- **`grep` 不校验沙箱边界、`file_glob` 可用 `..` 逃逸**；**`glob` 的 `..` pattern 可走出
  root**。现 grep 与 glob/file 工具同一边界校验，两者都拒绝 `..` 段。
- **docker 沙箱丢弃 `start_new_session`**：bash 超时只杀 docker 客户端所在的 harness 进程组，
  容器内命令继续跑。现把 `start_new_session` 透传到 `docker exec` 子进程。
- **cron 作业泄漏**：超时只 `process.kill()` 直接子进程、关闭时取消任务但不杀进程组；
  `communicate()` 还把全部输出读进内存。现独立进程组 + `killpg` + 取消时清理，输出改为
  有界 drain（256 KiB，保留尾部 2000 字符）。
- **`iterate report` 在没有报告时以 0 退出**：`--fail-on`（默认 `high`）形同虚设，review
  崩溃没写报告也能让 CI 变绿。现缺失报告 + 有 gate → exit 2（新增回归用例）。
- **`ITERATE_BASE_URL` / `ITERATE_API_FORMAT` / `ITERATE_PROVIDER` 被 profile 覆盖**：
  `materialize_active_profile()` 在应用环境变量之后又把 profile 的值投影回来，自建/ollama
  端点静默变成 Anthropic 客户端。现记录环境覆盖的字段并在投影时保留（新增回归用例）。
- **无效 `settings.json` 静默回落默认值，下一次保存覆盖用户配置**：一次笔误 + `ih auth login`
  会把 profiles/hooks/iterate 配置全部冲掉。现回落前将原文件保存为 `<name>.invalid-<ts>`；
  同时补 `OSError`/`UnicodeDecodeError` 兜底。
- **`GEMINI_API_KEY` 不被 auth 解析识别**；**非 UTF-8 `credentials.json` 让每次鉴权崩溃**；
  **插件 `.mcp.json` 的未知 transport 让插件加载崩溃**。三者均改为按项降级并告警。
- **聚合报告 `dimensions: []`**：`iterate_review aggregate` 不传维度，meta-review 把每条
  finding 判成 `DIMENSION_UNKNOWN`，凡有 finding 的报告都 “needs_revision”。现从 effective
  config 注入 `dimensions` 与 `goal`。
- **`converged` 不看 invariants**：不变量失败时仍以 “converged” 停止（CI 绿）。现
  `invariant_state == "fail"` 单独停止；未测量（None）保持原行为（新增用例）。
- **stall 硬停被标成 converged**：0 新 findings 且禁用 stall 暂停时，reason 以
  “converged” 开头，UI/report/CI 都当成功。现改为 `stalled: …`（新增用例）。
- **worktree 合并失败被吞掉**：`finalize` 只 log.warning，worktree 连同未合并的修复被删、
  运行却报正常停止。现返回成功与否，引擎在合并失败时明确报 FAILED。
- **worktree 清理可能 `rmtree` 任意目录**：`team.json` 里陈旧/手改的路径如果不是真 worktree
  （缺 `.git` 文件）会被递归删除。现拒绝并要求 `.git` 文件标记。
- **重复 subprocess agent id 静默顶掉前一个**：两次普通 `agent` 调用都是 `agent@default`，
  `send_message` 只能到后者。现拒绝重复的活跃 id 并说明如何区分。
- **`fix_scope` 不统计未跟踪文件**：新建 500 行模块也算 0 行，原子性检查形同虚设。现把
  `git ls-files --others --exclude-standard` 的行数计入。
- **Anthropic 客户端忽略 `timeout`/`ITERATE_TIMEOUT`**：只有 OpenAI 分支传了超时，
  挂死请求按 SDK 默认等待。现两分支都透传。
- **compact 附件路径正则把字母 `n` 当换行**：`[^)\n]` 在字符类里意为「非反斜杠/非 n」，
  `src/main.py` 被截成 `src/mai` 并被持久化重注入。现改为 `[^\n)]`（新增用例）。
- **CLI `-p/--print` 丢弃 `--permission-mode`**（关键）：`run_print_mode` 没把
  `permission_mode` 传给 `build_runtime`，`ih iterate run` 实际以 DEFAULT 运行，所有写操作
  被无人值守拒绝；错误提示还让人用那个不起作用的旗标。现透传（`--dangerously-skip-permissions`
  同样恢复作用）。
- **交互会话被普通错误打死**：`handle_line` 无论 `propagate_errors` 都重新抛出、`/命令`
  handler 异常（如 `/provider nosuch`）与后端请求循环都只捕 `CancelledError`。现命令异常
  转为错误行、backend 兜底为 `Session error … still healthy`，仅非交互契约才上抛。
- **`-p` 与 `--continue/--resume` 组合时静默丢弃 prompt**：现在直接报错退出，不再在 CI 里
  打开全屏 TUI。
- **print 模式向管道写 ANSI 清屏码**；**`--serve` 不带 `--html` 时静默无操作**；
  **`--allowed-tools "Read,Bash"` 不拆分**（Click 不拆 list，白名单静默失效）；
  **`--branch` 成功后不切回**（说 “for this run” 但其实留在目标分支）。均已修正/接线。
- **损坏的 `keybindings.json` 让启动崩溃**：现告警并回退默认键位。
- **`/voice on` 对不可用后端仍报 “enabled”**：保留偏好语义（`voice_enabled` 与
  `voice_available` 本就分离），但消息明确说明本构建无流式 STT。
- **`/output-style` 是死功能**：样式内容从不被消费。现把选中样式的正文注入 system prompt
  （`default` 不注入）。
- **WebUI `--project` 下所有只读路由 403**：非 chat 路由缺省回落到服务器 CWD 而不是启动
  项目，且前端把 403 误判为「令牌失效」。现统一回落到 `app.state.project_root`；
  SSE 401 判定仍只认 401。
- **重启后聊天记录为空**：`_chat_dir` 仅在首次 start 时设置。`/chat/history` 现接受
  `project_root` 并在无活跃运行时直接读该项目的 `web-chat.jsonl`。
- **用户答复晚于其触发的引擎活动落盘**：`set_result` 先唤醒引擎、再写聊天记录，重连回放
  永久固化错误顺序。现在解析 future 之前落盘/广播。
- **resume/stop 后残留旧 prompt 状态**：浅合并让决策按钮继续显示（点击变成把
  “resume” 当督促注入）且停止后仍显示「需要你的输入」。现 run-state 转出等待态即清空
  options/question/permission，按钮按 `waiting_for === "user_select"` 门控（新增前端用例）。
- **运行中/启动中的控制缺口**：`starting`/`pausing` 无停止按钮（后端本就支持）；现补齐。
- **权限姿态从不显示**：`permission_mode` 上线但没渲染，`full_auto` 与 `plan` 在界面上
  无法区分；现显示在运行卡片上，schema 默认值改回真实的 `default`。
- **失败横幅压过当前状态**：陈旧的 `chatStatus.error` 永远不清理，控制台接管后仍显示
  「运行失败」；现按「最近发生」排序。
- **SSE 缺口帧没有前端消费者**；**hub 的 resume 完整性用共享 id 计数器误判**（status 帧
  占号即误报不可续传）。现前端处理 `sse-gap` 并全量 REST 重同步，hub 以「最后一次被淘汰的
  hub 事件 id」判定完整性。
- **`FindingsResponse` 类型与后端漂移**（多出 `page`、缺 `returned/offset`）；**密钥列显示
  伪造的 18 字符长度**；**检查点“恢复”对话框承诺了不存在的重新武装**。均已对齐真实契约。
- **mypy 在 main 上红 gate**：`kernel_decision_record._refuse` 未标注 `NoReturn`，导致 10 处
  `object` 取值错误。
- **慢机竞态加固**：`send_message` 集成测试轮询窗口按 2.6.0 惯例 150×0.05s → 200×0.1s，
  `_wait_for_terminal_task` 2s → 5s。

### Changed

- 投递语义：给已完成的 agent 发消息会**重启并重投递**；进程在写入窗口内退出且无新输出时
  自动补投（见上）。
- `iterate report` 在缺失报告且启用 gate 时退出码由 0 改为 2（“未测量 ≠ 通过”）。
- 权限检查对 `bash` 命令串做路径提取与分段 deny 匹配——这是行为收紧，可能拒绝此前被放行的
  链式危险命令。
- 不变量失败现在会阻止 “converged” 停止；未配置/未测量不变量时行为不变。

### Added

- `web/roots.py`：所有只读路由共享的项目根解析（显式参数 > 启动项目 > CWD）。
- 回归测试：投递竞态（集成流程原用例转稳）、bash 分段 deny、命令路径提取 / 敏感路径 /
  deny 规则 / 风险区、notification matcher 生产形状、兄弟回滚新语义、空不变量 fail-closed、
  环境变量优先级、CI 无报告 fail-closed、stall≠converged、不变量失败阻断 converged、
  compact 路径正则、前端 stale-prompt 清理。

## [2.6.0] - 2026-10-15

### Added

- **CLI `--append-system-prompt` 真正接线**（`cli.py`、`ui/app.py`、`ui/backend_host.py`、
  `ui/react_launcher.py`、`ui/coordinator_drain.py`）：该旗标此前只解析不生效，现贯通
  print / 交互 REPL / task worker / React launcher 四条路径，coordinator 后续回合也经
  `session_system_prompt()` 继承追加内容。
- **`--restore-session <id>` 真实恢复会话**（`cli.py`、`ui/backend_host.py`、
  `ui/react_launcher.py`）：React 前端 `/resume` 此前声称恢复成功，但后端宿主从未加载
  快照——"恢复"出一个空会话（假恢复）。现 CLI 先校验会话存在于磁盘（缺失即报错
  exit 1），宿主经 `_replay_restored_transcript` 重放转写记录；缺 session id 或消息
  历史的残缺快照直接拒绝，不再装作成功。
- **MCP 工具名冲突确定性消歧**（`tools/__init__.py`、`tools/base.py`）：两台服务器
  清洗出同名工具时按 `__2/__3` 后缀登记并 WARNING（此前静默互相覆盖、路由串台）；
  `ToolRegistry.register` 拒绝重复注册（失败时原工具保持在位），64 字符 API 上限内
  确定性收敛。
- **回归测试**：新增 `tests/test_{permissions,hooks,engine,defensive}/test_hardening.py`、
  `tests/test_web/` 五个修复套件（start/stop 状态机、干预、SSE 续传、quick wins、
  chat 冲突）、`tests/test_tools/` 三个新工具套件、`tests/test_commands/test_cli.py`
  +31 例（restore/append/退出码/菜单索引/OptionInfo）、`tests/test_swarm/` 20 例、
  npm `bootstrap.test.js` +19 例（重定向降级 / 并发锁 / 超时 / venv 自愈）。

### Fixed

- **权限边界三处**（`permissions/checker.py`）：`denied_commands` 提前到
  `allowed_tools` 之前——白名单 `bash` 不再放行 `rm -rf *`；敏感路径匹配大小写折叠
  （`.SSH/id_rsa` 不再漏过内置 `*/.ssh/*`，拒绝方向只多不少）；`mode_override` 只认
  `plan`，`default`/`full_auto`/未知值回落配置模式（此前 exit_plan_mode 后会话被钉回
  DEFAULT，工具往 session metadata 写 `full_auto` 还能静默提权）。
- **引擎五处**（`engine/query.py`、`engine/query_engine.py`、`engine/messages.py`）：
  coordinator 上下文门控改匹配真实提示词（原 `startswith("You are a **coordinator**.")`
  对生产提示词永不命中，push/pop 协商从未生效，历史里留下错位副本）；权限提示串行化
  （`permission_prompt_lock`，并发工具不再叠对话框）+ 通道超时 fail-closed；工具输出
  offload 失败回落内联（不再炸掉回合）；`USER_PROMPT_SUBMIT` 的 `blocked` 真正拒绝——
  消息不再先入历史再被忽略，拒绝理由以 assistant 回合返回；`tool_metadata` 空 dict
  不再被 falsy 误替换，图片附件 20MB 上限。
- **hooks 执行器与热重载**（`hooks/executor.py`、`loader.py`、`hot_reload.py`）：
  单钩子异常与 spawn `OSError` 收敛为 failed HookResult（不再撕掉调用方正在进行的
  工具执行）；命令/HTTP 输出 8KiB 上限带截断标记；notification matcher 补
  `notification_type` 候选（`permission_prompt` 类 matcher 此前恒不命中）；未知 event
  名 WARNING 点名拼写并列出合法值（此前静默丢整段配置）；热重载失败保留旧注册表且
  不记 mtime（坏 settings 不会把钩子清空到下次改动为止）。
- **防御层两处**（`defensive/transaction.py`、`invariants.py`）：快照引用计数——同一
  文件并行变更时首个 commit 不再提前丢共享快照，兄弟变更的 rollback 仍能回到原始
  字节，锁覆盖整个读改写；refused 违规点名具体元字符与配置条目，操作者能修配置而不
  是被无从下手地锁死。
- **配置/认证/更新/定时任务 17 处**：run_lock 租约抢占竞态、settings env-key provider
  不一致与持久化、auth 存储错形状 JSON、update 四段预发布版本解析与空操作误报成功、
  cron daemon 启动竞态/损坏注册表日志/停止验证、config_loader YAML 解析警告与
  git 段校验、replay 时区归一、bool-as-int（`effort: true`、`line: true`）、onboard
  assert→raise、report_server 查询串别名、state store 监听器隔离、docker stop 超时、
  死字段 `budget_usd`/`max_turns` 回退与 `personalization_base_dir()` 接线。
- **Web 运行态 13 处**（`web/**`）：start 状态机守卫（pausing/stopping 叠加启动经
  `RunConflictError` 拒绝）、stop-during-start TOCTOU、`_restore_redacted` null 填充、
  结构化 409、阻塞调用移入线程、operator 暂停超时不再自动恢复、`_stop` 只终结 done
  注册、重置后历史读取与 journal 上限、权限审计缺口、hub 级 SSE id + `Last-Event-ID`
  续传与缺口告警、端口占用提示与 docs 门控。
- **CLI/UX 11 处**（`cli.py`、`ui/**`）：print 模式权限 fail-closed（无 TTY 的权限
  提示此前放行一切工具）、print 模式错误 exit 1 + stderr 摘要（此前吞错 exit 0、
  裸 traceback）、`--output-format` 白名单校验、菜单 0/负索引拒绝（此前回绕选中最后
  一项）、`OptionInfo`/`ArgumentInfo` 统一解包、`--cwd` 默认值改调用时解析（原 import
  期固化成启动目录）。
- **swarm/tasks 12 处**（`swarm/**`、`tasks/**`）：不可运行队友记 `failed` 而非桩
  成功；`_destroy_worktree`/`cleanup_team_directories` 包含化——git 失败、二进制
  `.git` 标记、不可读 team.json 不再外泄，团队目录在 finally 里清理；permission
  `request_id` 与 mailbox 消息 id 白名单校验（拒 `../`/NUL 穿越）；done-callback
  竞态不再误删重生队友的注册；spawn 自清终态书箱、`_generations` 随淘汰回收、
  `stop_task` 中止失败保留记录、git 调用加超时。
- **tools 10 处**（`tools/**`）：bash 超时杀整个进程组（`start_new_session` +
  `killpg`，孙进程不再握着管道挂死）；worktree 进出 60s 有界通信；`remote_trigger`
  名称/输出三重上限；notebook 单元 100K 字符上限；运行时 `assert` 校验改显式
  raise（`-O` 下不被剥离）；`exit_worktree` mypy 收口；顺带把
  `test_bash_tool_timeout_does_not_hang_when_stdout_stays_open` 的外层 2s 守卫提到
  20s（全量跑不再 flaky）。
- **npm 包装器 14 处**（`npm/lib/bootstrap.js`）：重定向 https→http 降级被
  `--allow-http` 门控（此前 302 可绕过安装源策略）；非 http(s) 协议前置拒绝；curl
  `--proto-redir =https`；下载失败/超时/中断清理流与半截缓存文件；响应中途 socket
  复位立即失败（此前干等 60s 总超时）；`detectPython` 30s / `runStep` 10min 超时且
  信号击杀如实报告（此前无超时可永久挂死、退出码报 null）；并发 bootstrap 互斥锁
  （原子创建 + 陈旧锁回收，venv 不再被并发写坏）；stamp 命中但 venv 缺失时自愈重装；
  主 pip 失败不再被 fallback 吞掉；损坏 `Location` 头不再 TypeError 崩溃。
- **验证**：全量 `pytest tests/ -q` **2627 passed, 6 skipped**（基线 2259 passed +
  1 flaky fail → 全绿）；ruff clean；mypy strict clean（254 源文件）；npm
  `node --test 'test/*.test.js'` **77 passed**。版本号在 `__init__.py` /
  `npm/package.json` / `frontend/web/package.json` / `CHANGELOG.md` 同步至 2.6.0。

## [2.5.0] - 2026-09-30

### Added

- **`dimension_context` 工具**（`iterate/dimension_context.py`）：kernel 契约
  `dimension_context` 的 Python 实现——~~把维度定义/证据要求注入给评审与协调链路，~~
  配套 `tests/test_iterate/test_dimension_context.py`。〔更正见 Unreleased：注入链路
  从未发生，该模块除自身与其测试外无人 import；接线是后续工作。〕
- **in-process 队友真正接进任务工具**（`swarm/in_process.py`、`tools/agent_tool.py`、
  `tools/send_message_tool.py`、`tasks/manager.py`）：`AgentTool` 按 `mode` 选后端
  （`in_process_teammate` → 进程内，缺失时回落子进程；`local_agent` /
  `remote_agent` → 恒子进程），spawn 时在 `BackgroundTaskManager` 注册可轮询
  `TaskRecord`，终态经 `mark_in_process_terminal` 发布（保留既有 `killed`，
  避免监听器重复触发），`stop_task` 走 `shutdown(agent_id, force=False)`，
  `write_to_task` 落到队友的文件邮箱，`SendMessageTool` 先查 in-process
  `is_active(name@team)`——此前这条路径注册了却无人消费。
- **发布产物 GPG 校验链**（`.github/workflows/npm-publish.yml`、`tools/gpg-signing.sh`）：
  release tarball 增加 `.asc` 验签（best-effort、防御纵深），npm publish 走
  provenance；`tools/gpg-signing.sh audit` 能点名 `secrets.GPG_PRIVATE_KEY` 为空
  导致整个签名步静默消失的位置。
- **回归测试**（`tests/test_tasks/test_manager.py`、`tests/test_tools/test_task_tools.py`、
  `tests/test_web/test_web_ux_hardening.py`、`frontend/web/src/__tests__/sseReconnect.test.ts`、
  `npm/test/postinstall.test.js`）：进程内队友注册/终态/停机/邮箱、console+辅助干预
  通道的 7 个 WebUI 缺口、SSE 重连与 401 判定（8 例）、npm postinstall 改为
  hermetic（`ITERATE_HARNESS_INSTALL_URL` + fake-python 夹具，不再真装）。

### Fixed

- **WebUI 操作台与辅助干预通道的 7 处真实缺口**（`web/api.py`、`web/events.py`、
  `web/hub.py`、`web/routes/config.py`、`web/routes/chat.py`、`web/run_manager.py`、
  `web/schemas.py` + 前端 `store.ts`/`api.ts`/`ConfigPage.tsx`/`RunStatusCard.tsx`）：
  - SSE 每帧补单调 `id:`（`_sse_frame`），重连时 `Last-Event-ID` 才有意义；
  - hub 订阅登记所属事件循环，`subscribe()` 顺带回收已关闭回路上的僵尸队列
    （`_reap_locked`），另加 `_MAX_SUBSCRIBERS=64` 兜底淘汰最老订阅——连接被硬拆
    而没跑到生成器 `finally` 时，队列不再被后续每次 publish 一直扇出；
  - `GET /config` 返回文件内容哈希 `version`，`PUT /config` 接受 `expectedVersion`，
    不一致直接 409：编辑器打开期间 YAML 被手改不再被静默覆盖（校验先于并发判定，
    409 不留备份文件、不写审计）；写入成功后 `detail.version` 回传新版本；
  - `POST /chat/reset` 补齐 `confirm=true` + `run.reset` 审计，并在 reset 前抓取
    `run_id`（`reset()` 会清空它），否则审计里只剩一句无法追溯的取消；
  - 注册 `@app.exception_handler(Exception)`：任何路由冒泡都走 `{"detail": ...}`
    JSON 契约（此前是 Starlette 的非 JSON text/plain 500，前端 `response.json()`
    解码失败只剩 "HTTP 500"）；已注册的 `RunManagerError → 409` 仍按类特异性优先；
  - `?token=` 仅在 `/api/v1/events` 生效，其余路由强制 `Authorization: Bearer`
    （token 不再随普通 URL 进访问日志/浏览器历史/Referer）；
  - `_publish_tool` 改为落盘 `web-chat.jsonl`（此前只广播不持久化），连接抖动或
    刷新后重建 transcript 时不再丢掉已经看过的工具调用卡片；
  - 前端：EventSource 拿不到失败响应状态码，令牌被拒与断网无法区分，原会永远重连
    且侧栏停在「重连中…」；现每次失败突发用一次 `/api/v1/status`（能带
    Authorization 头）探测，401/403 直接落到终态 `unauthorized` + 给出
    「重跑 `ih web serve`」的 toast（探测失败/未启用鉴权时保持原重连节奏）。
- **`pausing` / `stopping` 在前端不可见**（`RunStatusCard.tsx`、`styles.css`、
  `types.ts`）：这两个待定态既没有标签也没有样式，`npm run typecheck` 一直是红的，
  运行时标签渲染成 `undefined`——点了「暂停」看不出有没有被受理；现补中文标签、
  复用既有配色并用 `state-pending` 脉冲区分「已受理、等待轮次边界」。
- **`glob_tool` 回退遍历无界**（`tools/glob_tool.py`）：回退分支对整个根目录做
  `rglob` 再排序，项目大时会把内存吃光；改为 `islice(root.glob(pattern), limit)`
  后排序（O(limit)），回归用例断言的是"命中集合 + 长度"而非"全局最小 N 个"。
- **交互式会话被默认 200 轮硬截断**（`ui/runtime.py`）：`engine_max_turns` 原来
  恒取 `settings.max_turns`，控制台里手工探索会被静默打断；现改为
  `max_turns if max_turns is not None else (settings.max_turns if enforce_max_turns else None)`
  ——交互式默认无界，`--print` 与显式上限仍然生效（顺带修复 `9b2d326` 引入、
  此前就红的两条 `test_react_backend`）。
- **引擎轮次收敛时序**（`engine/query.py`、`coordinator/`）：轮次末尾才同步折叠
  （不再提前 tail fold）、协调器工具结果补齐尾部重排、图像预处理不再就地改写入参、
  reactive-compaction 重试不再吃掉一个 turn 槽位。
- **hooks 装载器**（`hooks/executor.py`）：结构化 hook 接受裸条目（不再要求
  `{hooks: [...]}` 包装），camelCase 事件键经 `_coerce_hook_event_key` 归一化
  （此前直接 ValueError）；prompt/agent hook 超时降级为可控失败而非抛穿。
  **超时实现改用 `asyncio.wait_for`**：原写法 `async with asyncio.timeout(...)`
  是 3.11+ API，在 3.10（`requires-python` 仍声明支持）上属性查找抛
  `AttributeError` 且被同一个 `try` 吞掉，于是**每个 prompt/agent hook 在 3.10
  上都静默退化成「hook failed」**——本机 3.13 全绿，只有 3.10 CI job 抓到。
  同时新增 `tests/test_py310_compat.py`（AST 静态守卫：扫 3.11+ 专属 API、
  认 `import asyncio as aio` 别名与 `from itertools import batched` 形态、
  用合法 310 写法钉住误报面、守卫自身有 planted 用例证明会失败）。
- **iterate 运行锁与落盘**（`iterate/run_lock.py`、`iterate/onboarding.py`、
  `iterate/personalization.py`）：新增 `<data_dir>/locks/` 下的磁盘租约 run-lock
  （WebUI 与控制台互斥，同一 project 不会两个循环），onboarding/personalization
  改原子写，report/validate 输出有界，worktree 清理、scope-chunk 校验补齐。
- **后台任务僵尸竞态**（`tasks/manager.py`、`tools/`）：`_ensure_writable_process`
  加竞态守卫，状态轮询更健壮，协调器工具名改为注册表里的真名。
- **配置合并与工具面**（`config/settings.py`、`commands/registry.py`、
  `services/`）：`denied_tools` 是 `disallowed_tools` 的别名却写进 `Settings` 顶层
  （stray attr，永不生效），现路由到 `permission.denied_tools` 并由 agent tool
  转发给 spawn 配置；cron 关闭时取消 in-flight 任务；session 文件损坏容忍；
  config 工具补 scope/强制转换/脱敏测试。
- **杂项正确性**（`301e14e`）：provider 用 `rsplit` 解析 provider-id（URL 里的
  `https://` 曾被切成两半）、bridge `max_active` 上限 + `work_secret` 走
  `urlsplit`、skills loader UTF-8 守卫、react launcher 读取 `OPENAI_API_KEY`。
- **收件箱轮询解码**（`swarm/mailbox.py`、`swarm/permission_sync.py`）：
  `send_permission_response_via_mailbox` 的文本信封现在可被 `poll_permission_response`
  经 `_decode_permission_response_payload` 解码（此前回答写了但读不出来）；
  time-out warning 打印真实 task id。
- **远端调用闸门**（`commands/registry.py`、`cli.py`）：`remote_invocation_allowed()`
  在模型驱动调用里真正生效，`/login`、`/config`、`/permissions`、`/bridge`
  远端一律拒绝，`--admin-opt-in` 显式放行管理员命令。
- **console/frontend 契约一致性**（`8bce462`）：WebUI chat status 经 run-lock 租约
  暴露"由外部控制台驱动"的运行（此前两个进程各跑各的却都显示空闲），
  run-manager 进程生命周期收紧，dashboard 与后端类型对齐。
- **guard 越界目标**（`b437933`）：pre-check 目标逃出 project root 时直接拒绝
  （此前只在执行期才拦）。

## [2.4.1] - 2026-09-25

### Fixed

- **陈旧客户端版本串**（`tools/web_fetch_tool.py`、`tools/web_search_tool.py`、
  `commands/registry.py`）：User-Agent 与 `/version`、`/upgrade` 兜底仍硬编码
  `IterateHarness/0.1.7`/`0.1`——现已改由 `iterate_harness.__version__` 派生
  （`importlib.metadata` 缺失时的回退也指向 `__version__`），站点与命令输出的
  版本号不再与实际发布脱节。
- **LSP 定位兜底逻辑错误**（`services/lsp/__init__.py`）：`extract_symbol_at_position`
  的第二段正则兜底恒返回行内**第一个**标识符、与光标位置无关（光标落在空白/标点时
  go-to-definition/hover 会解析到错误符号）——现改为按位置吸附最近的标识符；
  行内无标识符时返回 `None` 而非误报。

### Added

- **回归测试**（`tests/test_services/test_lsp.py`、
  `tests/test_tools/test_web_fetch_tool.py`）：新增 LSP 位置提取/吸附/越界用例，
  并为 web_fetch/web_search 的 User-Agent 增加"必须携带当前 `__version__`"断言，
  防止版本串再次悄然陈旧。

## [2.4.0] - 2026-09-22

### Added

- **迭代暂停无头提示**（`engine/query.py`）：headless/无交互通道会话到达 iterate
  暂停边界时，先经 hook bus 发出 `NOTIFICATION`（`notification_type=iterate_pause`，
  说明"无通道可应答，回退安全 STOP"）再执行既有的 headless 默认停——CI/后台
  运营不再面对无声停止（决策日志仍是持久审计）。
- **checkpoint 续跑提示**（`engine/query.py`）：正常停止（converged / round cap /
  budget）的 `StatusEvent` 在存在 `.iterate/checkpoint.json` 时追加
  "resume later rounds with /iterate resume" 提示，运营者可就近接回断点续跑流程。

### Changed

- **停止信号溯源覆盖**（`web/run_manager.py`）：`_permission_prompt`/`_ask_user_prompt`
  /`_ask_user_select` 全部加 `_stopping` 短路（立即 deny / 空答 / 默认选项），
  且 finally 只在 `not self._stopping` 时补发 "running" 状态——停止路径不再反向
  publish 一个误导性的 running；teardown 用 `asyncio.shield` 包裹
  `_flush_assistant_buffer`/`close_runtime`/最终 run-state publish/
  `_clear_stopping_if_owned`，CancelledError 时落位 `_stopping=True`；`_publish_chat`
  的聊天日志落盘移出事件循环（`asyncio.to_thread`）。
- **WebUI 状态快照缓存**（`web/events.py`）：`_build_status_payload` 每项目缓存
  2s（`_STATUS_CACHE_TTL`，线程锁 + 最多 64 项目），多开 SSE 连接不再每个 poll
  重复读整本 decision-log journal。
- **hub 优先事件不再被冲刷**（`web/hub.py`）：队列满时的 drop-oldest 改为优先丢弃
  `run-state` 以外的可弃事件（chat/progress 经 REST 可重同步）；只有队列全是
  run-state 时才让出最旧者——`paused+waitingFor` 驱动 WebUI 交互状态机，绝不能被
  chat 洪流静默冲掉。
- **worktree 删除停留检查**（`web/routes/workspaces.py`）：`remove_workspace` 拒绝
  删除项目当前活动（round ≥ 该项目最高 round）的 isolate worktree（HTTP 409），
  防中途删除正在使用的 sandbox/MCP；`_belongs_to_project` 提为列表/删除共用。
- **react launcher 密钥去向修正**（`ui/react_launcher.py`）：`api_key` 按
  `api_format` 注入——openai 系置 `OPENAI_API_KEY`，否则 `ANTHROPIC_API_KEY`，
  对齐 `settings.default_auth_source_for_provider`（此前 openai 端点被错误塞进
  ANTHROPIC 变量，启动的会话无凭证）。
- **bridge 日志有界**（`bridge/manager.py`）：复制的 replic be run 日志超
  `BRIDGE_LOG_MAX_BYTES`（64MiB）按尾 `BRIDGE_LOG_TAIL_BYTES`（16MiB）就地截尾，
  copy 循环内触发（防磁盘暴涨）。
- **任务管理器防幽灵/僵尸**（`tasks/manager.py`）：spawn 失败不再留永久 running 的
  ghost 记录（except 块标记 failed + 清理 waiter/locks/generations 后 re-raise）；
  output copy 失败不再把任务钉死在 running（try/except 后仍按 exit code 落终态）；
  `_trim_output_front` 移入 output lock 内、`read_task_output` 改 seek 尾读（不再整读）。
- **防御快照并发锁**（`defensive/transaction.py`）：`FileTransactionBuffer` 用
  `threading.Lock` 守卫 `_snapshots`，同路径并发 mutation（并行文件工具/工作线程）
  不再撕裂快照字典——first-snapshot-wins 原子生效。
- **权限敏感 read_file carryover 加固**（`engine/query.py`）：`_record_tool_carryover`
  对模型恶意的 `offset`/`limit` 用 `_coerce_nonneg_int` 安全转 int（畸形值不再把
  一次**成功**的 read_file 经 containment path 误判为工具错误）。

### Fixed

- **权限/暂停等待有界**（`engine/query.py`）：`permission_prompt` 增加与 pause 菜单
  一致的 `PAUSE_CHANNEL_TIMEOUT_SECONDS`（300s）有界等待，超时 fail-closed 拒绝——
  掉线的 UI 不再把 query coroutine 卡死；pause 菜单的 select/text 超时落 STOP 已有。
- **MaxTurnsExceeded 后工具结果不再丢失**（`engine/query_engine.py`）：tool round 刚
  结束就达 max_turns 时，`run_query` 已把 tool_result user message 追加进本地
  `messages` 但没有再发 `AssistantTurnComplete`——新增 `_sync_after_turn` 在同一
  事件与 `MaxTurnsExceeded` 两个路径都折回 `self._messages`，continuation 不再面对
  unmatched tool_use（provider 会 400）；coordinator 合成上下文依旧不落历史。
- **compaction 失败不再吞掉**（`engine/query.py`）：`_stream_compaction` 的
  `finally:{return}` 曾静默吞掉 `await task` 的异常并保留陈旧
  `(messages, False)`；改 try/except BaseException——正常路径传播真实失败，消费者
  早退（取消/生成器关闭）仍先 cancel 共享 messages 上的后台 compaction 任务。
- **runtime 关闭不再抛**（`ui/runtime.py`）：`close_runtime` 对 docker sandbox /
  MCP manager / SESSION_END hook 逐项 try/except（teardown 失败不再跳过后续资源与
  SESSION_END）；`build_runtime` 在 docker 启动失败时先关掉已连的 MCP 再 raise
  （fail_if_unavailable 语义保留，不再泄漏半成品）；`handle_line` 对未预期的
  submit 异常也容器化（log + 可见 message + 尽力快照），web 后端请求循环无 except
  也不再被逃逸异常击穿。
- **round 号输入钳位**（`iterate/review.py`）：`report_from_dict` 对模型返回的
  `round`/`maxReviewRounds` clamp 到 `[1, _MAX_SANE_ROUNDS=100]`（合法续跑号码仍过，
  但 `round: 999999` 不再让 `aggregate_rounds` 分配 ~1M 长 `findings_by_round`）。
- **grep 回退有界**（`tools/grep_tool.py`）：Python 回退既有 `limit` 又有
  `timeout_seconds`（`asyncio.wait_for` + `to_thread`），单行超
  `_MAX_PY_LINE_BYTES`（8MiB）/单文件超 `_MAX_PY_FILE_BYTES`（64MiB）跳过；
  二进制（`\0`）head 探测后跳过——灾难回溯正则/巨型 minified 不再挂死工具。
- **sandbox 临时文件跟踪清理**（`sandbox/adapter.py` + `utils/shell.py`）：每次
  sandbox settings 临时文件都登记进线程安全 set，`remove_runtime_settings` /
  `clear_stale_runtime_settings` / `atexit` 确保孤儿进程/spawn 失败不再漏临时文件。
- **iterate_review 只读语义精确化**（`tools/iterate_tools.py`）：`is_read_only`
  对 `aggregate`（会改 `context.metadata` loop-policy 状态）返回 False，`plan`/
  `meta-review` 返回 True。

## [2.3.0] - 2026-09-19

### Added

- **WebUI 会话可指定初始权限模式**（`web/schemas.py`，`web/routes/chat.py`，
  `web/run_manager.py`）：`POST /api/chat/start` 新增可选
  `permission_mode: "full_auto" | "plan" | "default"`（缺省 `full_auto`，无人值守
  不被 prompt 卡死），校验非法值报 422；`RunManager` 持久化 `permission_mode`
  并在 `_run_loop` 传给 `build_runtime`，允许 WebUI 以 plan/default 模式起步。
- **bridge 会话保留最近完成记录**（`bridge/manager.py`）：自然完成的会话不再
  立即从 `_sessions`/`_commands`/`_output_paths` 驱逐——UI 可在完成后读回
  transcript；仅显式 `stop` 的会话被驱逐，且完成会话池有界
  （`max_completed=20`，超出按最旧优先修剪，字典永不无限增长）。
- **npm 包装器下载完整性 fail-closed**（`npm/lib/bootstrap.js`）：自下载产物
  必须以 `.sha256` sidecar 核验通过才能交给 pip——sidecar 拉取失败、digest
  缺失/畸形、或不匹配一律删除产物并拒绝安装（唯一豁免是显式 `--no-verify`）；
  校验本次 `bootstrap.js` 全部加固项：下载/拉取校验和硬超时
  （响应级 + 总墙钟 60s）、非 2xx 一律拒绝、重定向预算 5 跳、缓存文件名
  消毒（版本号不允许 `..`/`/` 逃逸缓存目录）、venv 仅当 activate marker +
  interpreter 均存在才复用（否则整目录重建）、SIGINT/SIGTERM 下清理在途下载
  文件并把信号转发给 in-flight pip 子进程（本次把 `activeStepChildren`
  注册补上，先前为死代码）；npm `engines.node` 从 `>=16` 上调为 `>=18`
  （`AbortSignal.reason`、`node --test` 所需）。

### Changed

- **权限敏感路径边界扩展**（`permissions/checker.py`）：内置 `SENSITIVE_PATH_PATTERNS`
  新增 `*/.env`、`*/.env.*`、`*/.envrc`、`*/.netrc`、`*/.pypirc`、`*/.npmrc`、
  `*/.git-credentials`、`*/.config/gh/hosts.yml`。该边界在任何 allow/deny 规则与
  权限模式判断**之前**执行，永远无法被 override/allow-list 放宽。
- **`kernel.rollback` 调用修正**（`engine/query.py`）：defensive `rollback` 是同步
  方法，`CancelledError` 分支误写 `await`（运行期 await 一个非协程结果而直接返回、
  mypy 报错），改为同步调用后再 `raise`。

### Fixed

- **引擎 / 工具层**：`exit_plan_mode` 写 `"default"` 清理会话覆盖（而非 pop 造成
  残留）；`transaction` 回滚失败时保留 snapshot（不再抛第二异常吞掉原始错误）；
  `query.py` 终态 `final_message` 为 `None` 时产出 `ErrorEvent` 而非崩溃；
  `after_mutation` 遇 `CancelledError` 先回滚快照再上抛；`_WRITE_PAYLOAD_FIELDS`
  补 `"item"`；`FILE_MUTATING_TOOLS` 补 `"todo_write"`；`worktree_flow` 的
  `merge` 失败回退 `merge --abort`（无 merge 态时兜底清理）。
- **iterate 包**：`last_state` 对 `rounds`/`totalFindings`/`perDimension` 数值字段
  加固（非 int 降级）；`loop_policy.by_dim` 拒绝 bool 计数维度；
  `personalization` 的 `line: true` 不再被当作第 1 行、`focus` 仅接受 str 并去空白；
  `onboarding.update_completed_at_in_md` 只替换精确的两单元格元数据行；
  `trend_store.record_run` 新增 `covered_dimensions`——仅在覆盖维度集合存在时对
  集合内的缺失 finding 标记 fixed，空 `by_dimension` 保守不自动 fixed；
  `finding_fingerprint` 拒绝 bool line；`iterate_tools._record_trend` 作用域随后台
  发布 state 判断。
- **web**：`run_manager.send_message` TOCTOU 修复——锁内二次检查 `future.done()`
  与 registry 成员再 resolve，失效请求抛「该请求已超时或已失效」而非 500；finally
  守护 `close_runtime`（仅当 `self._bundle is bundle` 才关旧 bundle，避免新 run
  沙箱被误杀）+ 终态 run-state 发布（仅当 `self._task is task_handle`）；CJK
  否定词批准语义修复——「不想批准」「不能同意」正确视为拒绝、「没关系/没问题」
  仍为批准；`web/events.py` cursor `stat()` 包 `OSError`；
  `ui/backend_host.py` 无活动任务时中断输出明确提示、`task.cancel()` 后同步 await
  ack；`tasks/manager.py` 输出文件 8MiB 滚动截断（`_trim_output_front`）。
- **防御守卫（防崩溃）**：`plugins/loader.py` hooks/mcp JSON 损坏、顶层非对象、
  条目非 dict 全部安全降级不崩溃；`services/session_storage.py` 快照/列表/按 ID
  加载对 JSON 畸形、`stat` 竞争删除、payload 非 dict、消息条目形状错误全部
  try/except 兜底返回 `None`/跳过；`mcp/client.py` `result.content`/
  `result.contents` 可为 `None`、条目无法 `model_dump_json` 时降级 `str()`；
  `utils/file_lock.py` Windows 锁改用 `fstat`（消除按路径 `stat` 的删除竞态）。
- **遗留（Sep 18）未提交改动纳入**：`swarm/worktree.py` 陈旧目录清理 + 跨仓库
  删除守卫；`tests/test_swarm/test_worktree.py` +180 行测试。

### Verification

- 全量 pytest **2127 passed, 6 skipped**；ruff clean；mypy strict clean
  （246 源文件）；npm 包装器 **56 passed**。
- 版本号在 `__init__.py` / `npm/package.json` / `CHANGELOG.md` 同步至 2.3.0。

## [2.2.8] - 2026-09-17

### Fixed

- **`enter_plan_mode` / `exit_plan_mode` 工具只持久化全局设置、不作用于当前会话**
  （`tools/enter_plan_mode_tool.py`，`tools/exit_plan_mode_tool.py`，
  `engine/query.py`，`permissions/checker.py`）：
  两个工具原先调用 `load_settings()` / `save_settings()` 把权限模式写入
  `~/.iterate-harness/settings.json`——模型触发一次 plan-mode 切换会永久改变
  用户的全局默认权限模式（波及所有未来会话），但正在运行的会话的
  `PermissionChecker` 完全不知情，切换在本次会话内毫无作用（状态自相矛盾）。
  现改为**会话级**权限覆盖：
  - 工具把 `session_permission_mode` 写入执行上下文的 `metadata`，引擎在
    工具执行后合并回持久的 `tool_metadata`，下一个工具调用的权限检查即生效；
  - `PermissionChecker.evaluate` 新增 `mode_override` 参数（缺省保留原行为），
    敏感路径 / forbidden-fix pattern / risk area / allow-deny 等硬边界仍在
    模式判断之前执行，覆盖永远不会放宽这些硬限制；非法覆盖值降级为配置模式
    （fail-closed）；
  - `exit_plan_mode` 工具原本被归类为 mutating 工具（`is_read_only=False`），
    进入 plan 模式后会被 plan 模式自己封死、无法退出——现已改为 read-only，
    并清理会话覆盖恢复配置模式。
- **`toggle_voice_mode` 死代码 / 伪实现**
  （`voice/voice_mode.py`）：原实现 `return not enabled` 不读写任何状态，
  且全库无调用方，属伪实现。现实现为真实持久化的切换（读取当前设置、翻转、
  写回全局 settings，返回新状态）；`/voice` 命令与 Textual TUI 的
  `ctrl+v` 均改为调用该共享 helper，消除重复内联逻辑。
- **新增回归测试**（`tests/test_tools/test_plan_mode_tools.py`，10 例）：
  覆盖 enter 记录会话覆盖、不污染全局 settings、plan-覆盖拦截 mutating 且
  放行 read、exit 恢复配置模式、exit 工具为 read-only（plan 模式可退出）、
  full_auto 覆盖不放宽敏感路径、引擎 `_execute_tool_call` 全链路执行后
  下一个 mutating 调用被拦截等。

### Verification

- 全量 pytest **2111 passed, 6 skipped**；ruff clean；mypy strict clean
  （246 源文件）；npm 包装器 **56 passed**；前端 `tsc --noEmit` + vitest
  **13 passed**。
- 版本号在 `__init__.py` / `npm/package.json` / `CHANGELOG.md` 同步至 2.2.8。

## [2.2.7] - 2026-09-15

### Fixed

- **CLI `--permission-mode` / `--allowed-tools` / `--disallowed-tools` 无效**
  （`config/settings.py` `merge_cli_overrides`，`cli.py`，`ui/runtime.py` 等）：
  三个权限 CLI 标志仅写入 `Settings` 顶层的同名属性，未路由到实际使用的
  `Settings.permission` 子模型，导致命令行传入的权限模式/工具限制永远不会生效。
  现这三个参数从 `merge_cli_overrides` 中弹出后写入 `permission` 子模型；
  非法 `--permission-mode` 会降级为 `default` 并记录日志警告。
- **`--settings` 文件不存在时静默回退到默认配置**
  （`cli.py`）：`--settings` 指向不存在的路径时，`load_settings` 会忽略它
  并使用 `~/.iterate-harness/settings.json`，用户不知情。现 `main()` 在
  分发前校验文件是否存在，不存在则报错退出。
- **`merge_cli_overrides` 在 Settings 上产生残留属性**
  （`config/settings.py`）：`permission_mode`/`allowed_tools`/`disallowed_tools`
  被 `model_copy(update=updates)` 写入顶层实例，导致 `s.permission_mode` 永远
  存在但无人读取。现这三个 key 在 `model_copy` 前弹出。
- **`--name` / `--bare` / `--mcp-config` 死选项已移除**
  （`cli.py`）：三个选项无后端实现，误导用户。现已删除，同步更新 README。
- **npm 包装器下载产物无完整性校验**
  （`npm/lib/bootstrap.js`，`.github/workflows/release.yml`）：npm wrapper
  下载 wheel/tarball 后直接 pip install，无 SHA256 校验，受中间人/缓存投毒
  时可安装篡改产物。release.yml 现在在上传 wheel 时同步上传 `.sha256`
  sidecar；bootstrap.js 在 fallback 下载路径中先用 Node fetch 获取 sidecar、
  校验 sha256 后再 pip install；sidecar 不存在（旧版本）时降级为 warning。
- **插件命令 YAML 布尔值 `bool("false")` 为 True**
  （`plugins/loader.py` `_coerce_bool`）：`disable-model-invocation` /
  `user-invocable` 从 YAML frontmatter 读取后直接 `bool()` 转换，引号
  包裹的 `"false"` 字符串会使 `bool("false")` → `True`，静默违反作者意图。
  新增 `_coerce_bool`：对 `"false"/"no"/"off"/"0"/"none"` 返回 `False`，
  `"true"/"yes"/"on"/"1"` 返回 `True`，其余回退到 `bool()`。
- **swarm 邮箱路径穿越**
  （`swarm/mailbox.py` `validate_agent_id`，`get_agent_mailbox_dir`）：
  `get_agent_mailbox_dir` 的 `agent_id` 参数未做路径安全校验，包含
  `../` 的 agent_id 可逃出 `~/.iterate-harness/teams` 目录读写任意文件。
  新增 `validate_agent_id`（拒绝空字符串、NUL、`/`、`\`、`.`、`..`）并在
  `get_agent_mailbox_dir` 入口处强制调用。
- **bridge session_id 秒级时钟碰撞静默覆盖**
  （`bridge/manager.py`，`commands/registry.py`）：`/bridge spawn` 使用
  `bridge-%H%M%S` 格式，同一秒内多次 spawn 产生相同 ID，manager 的
  `_sessions` 字典被静默覆盖（旧进程句柄和 copy task 泄漏）。
  registry 现追加 6 位随机十六进制后缀；`BridgeSessionManager.spawn` 拒绝
  空或已存在的 session_id（抛 ValueError）。
- **hooks JSON 被 markdown 围栏包裹时静默阻塞事件**
  （`hooks/executor.py` `_strip_code_fences`，`_parse_hook_json`）：
  prompt/agent hook 返回的 JSON 常被模型包裹在 ````json ... ```` 中，
  `json.loads` 失败后降级为文本匹配，包含 `{"ok": true}` 的围栏输出
  被误判为失败。新增 `_strip_code_fences`，在 `json.loads` 前剥离围栏。
- **session_storage TOCTOU 与并发写无锁**
  （`services/session_storage.py`）：`load_session_snapshot`/`load_session_by_id`
  使用 `exists()` 后再 `read_text()` 的两步检查，竞态窗口内文件被删除或
  替换为 symlink 时会 raise 或读取攻击者数据；`save_session_snapshot`
  两步写入（latest.json + session-{sid}.json）无锁，并发保存导致读取者
  见到不一致的一对文件。现读路径改为 `try: read_text() except
  FileNotFoundError: return None`；写路径用 `exclusive_file_lock` 包裹。
- **cron 历史文件无界增长 + 关机时 in-flight 任务不取消**
  （`services/cron_scheduler.py`）：`cron_history.jsonl` append-only，永不
  裁剪；`ih cron stop` 发 SIGTERM 后等待仅 2s，超时 SIGKILL，in-flight
  任务的子进程被孤儿。现 `append_history` 在文件超过 5MB 时 under exclusive
  file lock 保留最近 5000 条；scheduler loop 追踪 in-flight tasks 并在
  shutdown 时 cancel + 等待 10s；`stop_scheduler` 等待 12s 后才 SIGKILL。

### Verification

- 全量 pytest **2098 passed, 6 skipped**；ruff clean；mypy clean（13 源文件
  专项检查 + 246 源文件全量）；npm 包装器 **56 passed**。
- 版本号在 `__init__.py` / `npm/package.json` 同步至 2.2.7。

## [2.2.6] - 2026-09-14

### Fixed

- **WebUI 权限解析把含 "no"/"not" 子串的普通英文词误判为拒绝**
  （`web/run_manager.py` `_parse_permission`）：`_DENY_MARKERS`/`_APPROVE_MARKERS`
  原先对整个归一化文本做子串匹配，使 "note"、"notebook"、"another"、"nothing"
  等普通词因为恰好包含 "no"/"not" 子串而被当成明确拒绝——即便前后还写着
  "please proceed" / "approve"。现 ASCII 标记改为**整词边界匹配**
  （`_has_ascii_marker`，正则 `\b…\b`），CJK 标记保持子串语义；中文否定前缀
  逻辑同步收紧为「直接否定批准词才翻转」（"没问题" / "没关系" 不再被
  "没" 前缀误翻成拒绝）。新增回归用例覆盖两类误判。
- **工具产物目录无界增长**（`engine/query.py` `_offload_tool_output_if_needed`）：
  超大工具输出落盘到 `~/.iterate-harness/data/tool_artifacts` 后永不清理，长会话
  可能累积成 GB 级死文件。现每次写产物后按保留上限修剪（默认 200 个最近文件，
  可用 `ITERATE_TOOL_ARTIFACT_MAX_FILES` 调整，下限 1），修剪为 best-effort、
  绝不阻断查询循环，新增 services 层用例。
- **`_clear_stopping_if_owned` 死代码收编**（`web/run_manager.py`）：`_run_loop`
  finally 原先内联等价逻辑，方法只被测试直接调用。现 finally 放开 lock 后统一
  调用该方法，消除重复并让所有权清理走同一处实现（既有测试保持不变）。

### Verification

- 全量 pytest **2082 passed, 6 skipped**；ruff clean；mypy strict clean（246 源文件）；
  npm 包装器 **50 passed**。
- 版本号在 `__init__.py` / `npm/package.json` / `frontend/web/package.json` /
  `CHANGELOG.md` 同步至 2.2.6。

## [2.2.5] - 2026-09-10

### Fixed

- **npm 包装器在无人值守/非 TTY 环境下首次运行会永久挂死**
  （`npm/lib/ui.js` 的 `askYesNo` + `npm/lib/bootstrap.js` 的 `runHarness`）：
  `askYesNo` 只监听 `rl.question` 回调，从不处理 EOF（`readline` 在 stdin
  被关闭/管道化/重定向 `< /dev/null` 时发出 `close` 事件）或无人应答的超时；
  而 `runHarness` 无条件以 `{ interactive: true }` 调用 `ensureRuntime`——于是
  在 CI、`ih ... < /dev/null`、`npx` 管道、cron 等无人可答的场景，首次运行
  （stamp 不匹配触发 bootstrap）会卡死在安装向导的 y/n 提示上，直到外层超时。
  现：
  - `askYesNo` 在 EOF（`rl.on("close")`）与超时（默认 120s，可用
    `options.timeoutMs` 覆盖，`0` 关闭）时回落到默认值并明确提示，绝不挂死；
    额外接受可注入的 `input`/`output`，便于测试；`timer.unref()` 不阻塞进程退出。
  - 新增 `interactiveSession()`（`process.stdin.isTTY && process.stderr.isTTY`），
    `runHarness` 仅在真正存在可交互终端时传 `interactive: true`；管道/CI 场景
    走非交互 bootstrap，不弹提示（安装失败仍可诊断，`ih` 下次运行会重试）。
  - 导出 `DEFAULT_PROMPT_TIMEOUT_MS` / `interactiveSession`，新增 5 个回归用例
    （EOF 回落、超时回落、显式 yes、`interactiveSession` 类型、默认超时常量）。

### Verification

- 全量 pytest **2073 passed, 6 skipped**；ruff clean；mypy strict clean（246 源文件）；
  npm 包装器 **50 passed**（bootstrap + postinstall + ui，新增 5 个用例）。
- 版本号在 `__init__.py` / `npm/package.json` / `frontend/web/package.json` /
  `CHANGELOG.md` 同步至 2.2.5。

## [2.2.4] - 2026-09-09

### Fixed

- **防御内核在工具抛异常时从未回滚**（`engine/query.py`，design §20.3.2）：`_execute_tool_call`
  对变更类工具先 `kernel.snapshot()` 再 `await tool.execute()`，此前若工具中途
  **抛出异常**（而非返回错误结果），`after_mutation` 永不执行——部分写入留在磁盘、
  挂起的快照既不提交也不回滚，`code` 模式的「原子事务 + fail-fast 回滚」保证被
  静默击穿。现以 try/except 包裹执行：异常路径先 `kernel.after_mutation(..., success=False)`
  回滚快照再上抛（回滚本身失败仅记录 warning），当前方调用方的异常包含逻辑照常将
  其转为工具错误。新增回归用例
  `test_mutating_tool_that_raises_rolls_back_snapshot`。
- **`ui/backend_host.py` 依赖 `assert self._bundle is not None` 控制流**：8 处断言在
  `python -O` 下会被剥离，使防护静默消失并退化成混乱的 `AttributeError`。全部改为
  显式 `if self._bundle is None: raise RuntimeError(...)` 守卫（其中 inert 的
  `_emit_last_loop_state` 改为静默返回）。
- **`config set timeout` 类型误判**（`tools/config_tool.py`）：`timeout` 实为
  `Settings.timeout: float`，却与整型键同置于 `_INT_KEYS`——小数（`30.5`）触发
  `int()` 抛错、原始字符串被写进浮点字段直到下次重载才纠正。新增 `_FLOAT_KEYS`
  独立浮点强转。
- **meta-review 对续跑的 `ROUND_GAP` 误报**（`iterate/meta_review.py`）：`_check_round_shape`
  假定序列恒从 round 1 开始，而 resume 会话携带连续片断（如 `[4,5,6,7]`）——此前
  把合法的缺失前序片段误判为缺口、把一致报告翻转成 `revise`。改为只校验**片段内部**
  的相邻缺口（`present[i]+1 == present[i+1]`），不再要求从 1 起连续。
- **coordinator 模式的上下文消息破坏了 provider 的 tool_use/tool_result 顺序**
  （`engine/query.py`）：每次回合把 `# Coordinator User Context` 追加在
  final assistant 消息之后、tool result 之前——而 provider 要求 `tool_result`
  必须紧跟在对应 `tool_use` 之后，中间插入一段纯文本 user 消息会造成乱序拒绝。
  现把 coordinator 上下文改在 tool result 之后回落为尾消息（无 tool call 的路径
  保持行为），并新增断言校验工具结果与 tool_use 之间不再夹杂上下文。
- **`Mailbox.mark_read` 吞掉「消息不存在」的结果**（`swarm/mailbox.py`）：
  内部 `_mark_read` 返回是否找到并标记的 bool，却从未回传——调用方无法分辨
  「已标记」与「id 早已消失」，静默漏标。现 `mark_read` 返回该 bool。
- **`glob` 沙箱校验用错了边界**（`tools/glob_tool.py`）：docker 沙箱激活时
  `validate_sandbox_path(root, Path("."))` 以**进程工作目录**为边界而非
  `context.cwd`（项目根）——从不同目录启动时会误拒项目内搜索或误放越界搜索。
  现把 `project_root` 透传入 `_glob` 并以其为边界。
- **`bash` 工具无上限缓冲 stdout**（`tools/bash_tool.py`）：`_read_remaining_output`
  用 `process.stdout.read()` 一次读满整个管道，`cat`/构建日志可撑爆内存；现按
  每次 10 MB 分块读取并在该上限处停止。
- **`IterateLoopPolicy.cost_meter` 用 `field(default=None)` 强塞非可选类型**
  （`iterate/loop_policy.py`）：消除 `# type: ignore[assignment]`，改为 `_cost_meter`
  私有后备 + 类型化只读 `cost_meter` 属性（读取时若未初始化抛 `RuntimeError`），
  `__post_init__` 负责填充，mypy strict 归零。
- **死 `TYPE_CHECKING: pass` 脚手块**（`swarm/registry.py`、`swarm/subprocess_backend.py`、
  `swarm/types.py`）：删除无任何导入内容的空 `if TYPE_CHECKING:` 块与其 `TYPE_CHECKING`
  导入。
- **非交互 `run_print_mode` 的 `_clear_output` 是纯 no-op**（`ui/app.py`）：会话恢复等
  请求清屏的命令在 `text` 输出下实际清屏（ANSI `\x1b[2J\x1b[H`），`stream-json`
  输出下发 `{"type":"clear_screen"}` 事件；管道 worker 的 no-op 补注释说明其语义。
- **npm 包装器 `postinstall` 只认 `SKIP_INSTALL=1`**（`npm/scripts/postinstall.js`）：
  与 `bootstrap.ensureRuntime`（`1/true/yes/on`，忽略大小写）对齐，消息不再硬编码
  `=1`。

### Changed (tooling / CI)

- **npm 包装器测试纳入 CI**（`.github/workflows/ci.yml`）：新增 `npm-wrapper` job 运行
  `npm test`（此前包装器测试从未在任何 CI 平台执行）；`package.json` 的 `test` 脚本
  补入一直存在却从未运行过的 `test/ui.test.js`，并在 `files` 白名单纳入
  `README.zh-CN.md`。

### Verification

- 全量 pytest **2073 passed, 6 skipped**（新增 1 个防御内核回滚回归用例）；ruff clean；
  mypy strict clean（246 源文件）；npm 包装器 **45 passed**（bootstrap 36 + postinstall 3
  + ui 6，现含此前未执行的 ui 用例）。
- 版本号在 `__init__.py` / `npm/package.json` / `frontend/web/package.json` /
  `CHANGELOG.md` 同步至 2.2.4。
- 前端构建与 `tsc --noEmit`、vitest 不受本次变更影响（未触碰前端源码）。

## [2.2.3] - 2026-09-08

### Fixed

- **WebUI 停止确认消息在取消竞态下可能丢失**（`web/run_manager.py`）：`_run_loop`
  的 `asyncio.CancelledError` 分支在任务正被取消时 `await _publish_chat` /
  `_set_state`，取消信号会再次传播导致「运行已被用户停止」永远到不了用户。
  现用 `asyncio.shield` 包裹这两笔写入，保证停止确认可靠送达；若外层再次被
  取消则落位 `_stopping` 并记录 warning。
- **`iterate.config.yaml` 空 `invariants` 段静默禁用校验**（`iterate/config_loader.py`）：
  用户写 `invariants: {}`（或 `commands: {}` / `ensure: []` 全空）时，此前会产出一个
  **「已配置但什么都不做」** 的空 `InvariantConfig`，防御内核据此不再回退到
  `validation.commands`，项目失去 post-edit 校验。现在空 invariants 段降级为
  `None`，内核正确回退到 `validation.commands`（非空 `ensure`/`commands` 仍为真实
  配置）。新增 `test_invariants_section_empty_falls_back_to_none` 与
  `test_invariants_partial_empty_stays_configured` 回归用例。
- **配置解析错误被静默吞掉**（`iterate/config_loader.py`）：`dimension_resources` /
  `token_budget` / `budget_usd` / `max_turns_per_minute` / `worktree_isolation` /
  `thresholds` 六类字段解析失败时错误数组只赋给 `_` 前缀变量从不透出，配置里的
  typo 静默回退默认值。现逐一 `log.warning` 记录具体错误，用户配置漂移不再无声。
- **CLI `ih iterate report` 无报告条目时 `assert` 控制流**（`cli.py`）：`init` 的
  `assert chosen is not None` 在 `python -O` 下会被剥离，改为显式 `RuntimeError`
  守卫（该分支 `# pragma: no cover`，正常循环必不会触发）。
- **`ITERATE_LOG_LEVEL` 非法值静默回退**（`cli.py`）：不认得的日志级别名默认落到
  `WARNING` 且无提示，现打印一条 stderr 警告说明已回退。

### Changed

- **CLI 帮助文本补全**（`cli.py`）：`ih iterate --help` 概要从 11 个子命令补全到
  全部 18 个（新增 doctor/status/validate/sessions/cron/refresh/reonboard）；
  `web_app` 帮助移除内部 `design §17` 引用。
- **`--theme` 持久化可见**（`cli.py`）：`ih --theme <name>` 写全局配置时在 stderr
  打印已持久化提示，避免用户对一次性 flag 永久改配置感到意外。
- **防御工具公开面**（`tools/iterate_tools.py`）：`__all__` 补上
  `IterateAssumptionTool`（此前运行时已注册但星号导入不可见）。

### Fixed (npm wrapper)

- **`packageVersion()` 损坏 manifest 崩溃**（`npm/lib/bootstrap.js`）：`package.json`
  缺失/坏 JSON 时此前抛出未捕获异常，现回退 `0.0.0-unknown` 并打印提示，保证
  `ih` 调用不因 manifest 损坏而整体崩溃。
- **信号处理器累积**（`npm/lib/bootstrap.js`）：`SIGINT`/`SIGTERM` 转发器在子进程
  `close` 时未清理；重复 `runHarness`（如同进程测试）会堆叠重复转发。现子进程
  close 后 `removeListener` 清理。
- **`ITERATE_HARNESS_SKIP_INSTALL` 只认精确 `"1"`**（`npm/lib/bootstrap.js`）：
  现在识别 `1/true/yes/on`（忽略大小写），符合常见 truthy 约定。

### Verification

- 全量 pytest **2072 passed, 6 skipped**（新增 2 个 invariants 回归用例）；ruff clean；
  mypy strict clean（246 源文件）；npm 包装器 45 passed（bootstrap 36 + postinstall 3
  + ui 6）；前端 `tsc --noEmit` + vitest 13 passed。
- 版本号在 `__init__.py` / `npm/package.json` / `frontend/web/package.json` /
  `CHANGELOG.md` 同步至 2.2.3。

## [2.2.2] - 2026-09-07

### Fixed

- **Python 3.10 import 回归**（`state/store.py`）：`from typing import Unpack`
  在 Python < 3.11 上不存在，而包声明 `requires-python = ">=3.10"`（npm 包装器
  也接受 >= 3.10 的解释器）——任何 3.10 安装都会在导入期
  `ImportError: cannot import name 'Unpack' from 'typing'` 崩溃（CI 3.10 矩阵
  暴露，此前已连续失败）。改为版本守卫导入：3.11+ 用 `typing.Unpack`，3.10
  回退 `typing_extensions.Unpack`；`typing-extensions==4.16.0` 作为显式锁定
  运行时依赖加入 `pyproject.toml`（此前仅经 pydantic 传递可用）。本机
  Python 3.10.10 实测 `AppStateStore.set()` 正常。新增
  `tests/test_state/test_app_state.py` 3.10 路径回归用例。

### Verification

- 全量 pytest **2069 passed, 6 skipped**；ruff clean；mypy strict clean
  （246 源文件）；Python 3.10.10 冒烟导入通过。
- 版本号在 `__init__.py` / `npm/package.json` / `frontend/web/package.json` /
  `CHANGELOG.md` 同步至 2.2.2。

## [2.2.1] - 2026-09-07

### Fixed

- **WebUI 干预通道无人值守挂死**（`web/run_manager.py`）：`_ask_user_prompt`
  与 `_ask_user_select` 与 `_permission_prompt` / `ui/backend_host._ask_question`
  的 300s 有界等待对齐——循环在无人值守的 WebUI 中暂停等待问题答复或菜单选择
  时不再无限挂起。超时自动落入安全默认：自由文本问题返回空答复（引擎视为
  「按默认继续」），选择菜单返回首个选项值（文档化的 Esc-cancel 安全默认，
  即 continue/resume），并推送一条 `chat status` 消息让操作台可见「已超时自动
  继续」。新增 `tests/test_web/test_run_manager.py` 三组有界等待回归测试。
- **会话列表对损坏 time 字段的崩溃**（`cli.py` / `services/session_storage.py`）：
  `ih iterate sessions` 遇到 `created_at` 为字符串/缺失值时，`fromtimestamp`
  抛 `TypeError`/`OverflowError`（现一并捕获转 `unknown`）；`list_session_snapshots`
  排序时混合字符串与 float 的比较崩 `TypeError`（现新增 `_coerce_mtime` 统一
  规整为可排序的 epoch float，非法值回退文件 mtime）。新增
  `tests/test_services/test_session_storage.py` 损坏字段回归测试。
- **`ui/runtime.py` 冗余异常子句**：`except (ValueError, Exception)` 中
  `ValueError` 是 `Exception` 的子类，冗余消除为 `except Exception`。

### Verification

- 全量 pytest **2069 passed, 6 skipped**；ruff clean；mypy strict clean
  （246 源文件）；npm 包装器 `bootstrap.test.js` 36 passed。
- 版本号在 `__init__.py` / `npm/package.json` / `frontend/web/package.json` /
  `CHANGELOG.md` 同步至 2.2.1。

## [2.2.0] - 2026-09-06

### Added

- **in-process teammates now run the real agent loop**（`swarm/in_process.py`）：
  新增 `build_teammate_query_context`，按 `TeammateSpawnConfig` + 当前 settings
  组装完整引擎接线（API client / tool registry / permission checker / system
  prompt），`InProcessBackend.spawn` 自动装配并交给 `_run_query_loop` 执行——
  此前 in-process 队友在无预置 context 时只会跑「日志 + sleep」的 stub，任务
  从未真正执行。`code` 模式的防御内核（design §20.5）随 `task_mode` 一并继承；
  仅在缺 API key / settings 不可读时回退为取消感知的 no-op（保留测试与直接调用
  的无密钥行为），并显式记录回退原因。新增
  `tests/test_swarm/test_in_process.py` 三组用例（完整接线 / 防御内核继承 /
  无密钥降级）。

### Fixed

- **changed-only 审查漏掉非 ASCII 文件名**（`iterate/git_scope.py`）：git 默认
  `core.quotepath=true` 会用 C 风格八进制转义引用中文等非 ASCII 路径
  （`"\346\226\207..."`），转义串无法对回磁盘文件，导致该文件静默掉出
  `--changed` 变更清单。所有 porcelain 读取现强制 `-c core.quotepath=false`
  输出原始 UTF-8 路径；新增回归测试
  `test_collects_non_ascii_filenames`。
- **deferred 架构清单漏读历史 report 形状**（`iterate/decision_log.py`）：
  `extract_deferred_architectural` 只认 `findings` 键，改用共享的
  `findings_from_report` 兼容 `topFindings`/`notableFindings`/嵌套 `summary`
  三种历史布局，resume 时不再丢遗留的架构级待办；新增回归测试。
- **`--branch` 隔离 worktree 泄漏**（`cli.py`）：`_ensure_review_branch` 的回退
  worktree 用完从未清理，会在 `.git/worktrees` 里不断堆积。返回值改为
  `_BranchSwitch`，调用方 `finally` 里 `restore()` 恢复 cwd 并按「仅当干净才
  `git worktree remove`（绝不 `--force` 丢弃未提交改动）否则打印移除提示」清理。
- **两个环境相关 flaky 测试**：`test_subagent_stop_hook...` 轮询期限 2s →
  15s、`test_task_and_todo_flow_across_registry` 输出等待 2s → 20s，消除全量
  suite 并行负载下的偶发失败。
- **WebUI 冗余收敛条件**（`frontend/web/src/store.ts`）：`notifyRunEnd` 的
  `converged || converged === true` 恒等于 `converged`，简化。
- **agent shell 执行不再加载用户 rc（login-hook 秒级开销，极端负载下
  直接打穿 5s 任务超时）**（`utils/shell.py`）：`bash` 分支默认改传
  `--noprofile --norc`，子进程直接继承父进程（ih 启动时已解析）的完整
  环境——`~/.bash_profile`/`~/.bashrc` 里的 conda init / nvm / pyenv 等
  hook 不再为**每一条** agent 命令（bash 工具、task manager、hook、cron、
  bridge、remote trigger）重复付费。实测本机单条命令从 **9.9s 降到 0.009s**
  （~1000×）。需要旧行为可用 `ITERATE_HARNESS_SHELL_LOAD_RC=1` 逃生阀
  恢复 `-lc`。同步收紧 `script` PTY 包装、`_bash_is_usable` 探测与 Windows
  git-bash 分支，新增逃生阀测试。
- **真实子进程测试预算过紧**（`test_manager.py` / `test_task_tools.py`）：
  task 等待统一从 5s/2s 放宽到 30s，消除高负载 CI 上的偶发超时失败
  （修复后这些文件 **40 passed in 5.5s**）。

### Verification

- 全量 pytest **2065 passed, 6 skipped**；ruff clean；mypy strict clean
  （246 源文件）；npm 包装器 `bootstrap.test.js` 36 passed；前端
  `tsc --noEmit` + vitest 13 passed。
- 版本号在 `__init__.py` / `npm/package.json` / `frontend/web/package.json` /
  `CHANGELOG.md` 同步至 2.2.0。

## [2.1.1] - 2026-09-06

### Fixed

- **预算暂停（budget pause）注入信息修正**（`iterate/loop_policy.py`）：预算暂停在
  `inject_message` 注入下一轮审查指令（`_build_next_round_message`）而非人工菜单文本，
  恢复后模型可继续审查流程。
- **Kimi/Moonshot 工具调用兼容**（`api/openai_client.py`）：`stream_options` 仅对
  `kimi` / `moonshot` 前缀模型弹出（`_is_kimi_tool_call_provider`），其余提供商恢复
  usage 跟踪。
- **WebUI 配置恢复截断修正**（`web/routes/config.py`）：配置项与来源 `zip` 对齐改为
  `zip_longest`，入参列表更长时不再截断/丢项。
- **工作区（worktree）状态同步**（`iterate/worktree_runtime.py` / `worktree_flow.py`）：
  - `finalize()` 在异常中止路径也执行项目状态同步回写（仅提交仍限定 merged）。
  - `merge --ff-only` 失败时回退 `git merge --no-edit`（以 merge commit 合入）。
  - `except TimeoutError` 改为 `except asyncio.TimeoutError`，兼容 Python 3.10。
- **fix_scope 原子最大行数来源修正**（`engine/query.py`）：使用
  `load_effective_config(...).config.atomic.max_lines`，缺省回退
  `DEFAULT_ATOMIC_MAX_LINES`；单一工具调用的异常被封装为 `ToolResultBlock`
  （`CancelledError` 仍上抛）。
- **配置加载**（`iterate/config_loader.py`）：`max_rounds` 排除布尔值。
- **证据核验二进制判定**（`iterate/evidence.py`）：含 NUL 字节的文件在整文件
  （whole-file）核验下不再误报 `out-of-range`。
- **记忆删除入口保护**（`memory/manager.py`）：`remove_memory_entry` 排除
  `MEMORY.md` 入口点，入口文件不可再被删除。
- **notebook 编辑容错**（`tools/notebook_edit_tool.py`）：JSON 损坏的 `.ipynb` 返回
  明确的工具错误信息，不再抛未捕获 `JSONDecodeError` 中断整个查询。
- **cron 列表容错**（`tools/cron_list_tool.py`）：`name` / `command` 缺失时回退
  `<unnamed>` / `<unknown>`。
- **团队名路径穿越防护**（`swarm/mailbox.py`）：新增 `validate_team_name`，拒绝空、
  NUL、`/`、`\`、`.`/`..`；`get_team_dir` 强制校验，封堵 agent 驱动的目录穿越。
- **swarm 收件箱修正**（`swarm/in_process.py`）：`send_message` 以完整 `agent_id`
  定位收件箱（修复名前不匹配）。
- **checkpoint 时间戳**（`iterate/checkpoint.py`）：payload 新增 `timestamp`。
- **事件轮询尾行处理**（`web/events.py`）：无 `\n` 结尾的末行在下轮轮询重读，
  不再永久跳过。
- **配置工具**（`tools/config_tool.py`）：`show` 将 `api_key` 脱敏为 `<redacted>`；
  `set` 将整型键（max_tokens 等）按 int 类型写入。
- **`_ask_question` 超时保护**（`ui/backend_host.py`）：`asyncio.wait_for(future,
  timeout=300)`，超时返回 `""` 不再挂死。
- **进程任务 API key 传递**（`tasks/manager.py`）：API key 改经子进程环境变量
  `ANTHROPIC_API_KEY` 传递，不再暴露在命令行参数。
- **路径展开与远端提供商**（`tools/image_to_text_tool.py` / `auth/manager.py`）：
  `~` 先 `expanduser` 再拼接 `cwd`；新增 `orcarouter` 提供商（env `ORCA_KEY`）。
- **keyring 类型检查**（`pyproject.toml` / `auth/storage.py`）：经 `[tool.mypy]`
  overrides 忽略 keyring 缺失导入，环境无关且 mypy strict 归零。

## [2.1.0] - 2026-09-05

### Added

- **CLI 新子命令 `ih iterate validate <command>`**：运行预配置的校验命令并输出
  `allowed` / `reject_reason` / `exit_code`，供 CI / 脚本编排防御式 pre-check
  与 post-check（此前只能经 REPL 斜杠命令访问）。
- **CLI 新子命令 `ih iterate sessions`**：列出已存会话（summary / model /
  时间戳 / 消息数），支持 `--limit` 与 `--json`，并提示 `ih iterate resume
  --session <id>` 续接。
- **`ih iterate review` / `ih iterate run` 新增 `--template` 标志**：可在
  `standard`（默认）/ `strict`（保守）/ `quick`（快速）三种审查模板间切换，
  模板预设此前已内置但 CLI 不可达。

### Fixed

- **安全加固（tools）**：
  - `config_tool` 新增 `_ALLOWED_CONFIG_KEYS` 白名单，拦截通过 `setattr`
    的任意属性注入。
  - `bash_tool` / `image_to_text_tool` / `glob_tool` 对工作目录与文件路径
    补充沙箱（sandbox）越界校验。
  - `web_search_tool` 对 `search_url` 主机名做白名单校验，封堵 SSRF。
  - `enter_worktree_tool` / `exit_worktree_tool` 由阻塞 `subprocess.run`
    改为 `asyncio.create_subprocess_exec`，消除异步事件循环死锁风险。
- **健壮性（内核 / 服务）**：
  - `swarm/mailbox.py` 以 `asyncio.get_running_loop()` 替换废弃的
    `asyncio.get_event_loop()`。
  - `config/settings.py` `resolve_api_key()` 现优先解析 profile 级
    auth 源；env 数值解析与 malformed JSON 均安全降级并告警；Docker
    env 覆盖改为合并而非整体替换。
  - `services/session_storage.py` 对 `session_id` 加入格式白名单，防路径穿越。
  - `report_server.py` 改为每次调用绑定 handler 子类，消除类级状态竞态。
  - `defensive/transaction.py` 以不可碰撞的 UUID sentinel 替换脆弱的
    字符串哨兵。
  - `permissions/checker.py` `_project_personalization` 注入 `cwd`，不再
    依赖进程级 `Path.cwd()`。
  - `sandbox/session.py` atexit 清理改为安全的同步包装。
  - `defensive` 契约中的 `_sanitize_metadata` 去重，统一委托
    `session_storage` 实现。
- **CLI / TUI UX**：
  - 运行结束提示改用非阻塞的 `--html` 报告命令。
  - headless 模式启动时打印目标与进度预览。
  - `--changed` 无变更时给出可操作提示（`--clean-ok`）。
  - `iterate_app` 帮助文本补全全部子命令；resume 会话缺失时提示
    `ih iterate sessions`。
  - TUI 命令选择器加入 `/iterate`。

### Verification

- 全量 pytest：**234 passed, 2 failed, 1 skipped**（`test_swarm/test_spawn_utils.py`
  两项 `TypeError: build_inherited_cli_flags() got an unexpected keyword argument
  'task_mode'` 为既有失败，发版前经 git stash 复核与本版无关）；ruff clean。

## [2.0.1] - 2026-09-04

### Fixed

- `iterate/config_loader.py`：修复 `invariants` 配置段未被解析的关键 bug。此前
  `config_from_dict` 不解析 `invariants`，导致 `effective.config.invariants`
  恒为 `None`，code 模式下防御式内核永远收不到项目不变量。现在：
  - `invariants` 段（`ensure` + `commands`）正确解析进 `IterateConfig`；
  - 段内字段形状错误（如 `commands: not-a-dict`）整体降级为 `None`，让内核
    回退到 `validation.commands`，避免出现 `invariants_configured=True` 的
    误导状态。

### Verification

- 全量 pytest：**2049 passed, 6 skipped**；ruff clean。新增回归测试：
  `test_invariants_section_parsed_from_config` /
  `test_invariants_section_absent_stays_none` /
  `test_invariants_section_malformed_falls_back_to_none`
  （`tests/test_iterate/test_config_loader.py`）以及端到端的
  `test_kernel_resolves_invariants_from_disk_config`
  （`tests/test_defensive/test_defensive_kernel.py`）。

## [2.0.0] - 2026-09-04

### Added

- **双模式架构（dual-mode，设计 §20.2）**：`task_mode`（`code` / `iterate`）与
  `permission_mode` 正交。`iterate` 保留 1.x 审查/迭代循环；`code` 为通用编程
  agent 模式，全套工具照常可用并叠加防御式编程保证。
- **code 模式防御式内核（`defensive/kernel.py`，设计 §20.3.2）**：
  - 原子事务缓冲（`defensive/transaction.py`）：每次变更类文件工具执行前快照，
    失败自动回滚（fail-fast + 原子事务）。
  - 不变量守护（`defensive/invariants.py`）：编辑成功后重跑项目不变量
    （`invariants.ensure` 文件断言 + `invariants.commands` 命令列表；无
    `invariants` 段时回退 `validation.commands`），违规回滚并作为工具错误上抛。
  - 假设审计（`defensive/assumptions.py`）：agent 声明的假设写入决策日志，
    falsified 视为 fail-fast 信号。
- **`record_assumption` 工具**（`tools/iterate_tools.py`）：声明/验证假设并写入
  `.iterate` 决策日志，形成完整审计轨迹。
- **worker 防御内核继承（设计 §20.5）**：`--task-mode` 经 CLI → AppState →
  `agent_tool` → subprocess backend 全链路穿透，code 模式 leader 派生子代理
  以相同防御内核运行。
- **TUI 模式切换（设计 §20.6）**：空输入 Tab 循环 `code` ↔ `iterate`，输入框
  左侧竖向模式色条 + 底部模式文字随模式变色（code=primary / iterate=amber）。
- **配置扩展**：`IterateConfig` 新增 `invariants` 段（`ensure` 文件断言 +
  `commands` 逐模块命令表），项目可声明自定义不变量。

### Fixed

- `config_loader.py`：清理未使用导入。
- `engine/query.py`：补充 `DefensiveKernel` 显式类型导入（修复 mypy strict）。

### Verification

- 全量 pytest：2045 passed, 6 skipped；ruff clean；mypy clean（仅 keyring stub
  环境噪声）；npm 包装器 36 passed；前端 tsc clean；wheel 含 `defensive/` 与
  `_frontend_web` 产物。

## [1.16.3] - 2026-08-31

### Fixed

- **mypy strict 全量归零**：修复 40+ 模块的严格类型错误（泛型类型参数、Liskov
  兼容的方法签名、TypedDict/`Unpack`、精确集合注解、清理失效 `type: ignore`），
  并在 CI quality job 新增 `mypy src` 严格门禁，杜绝类型错误回归。
- **能力/技能加载正确性**（`skills/loader.py`、`skills/__init__.py`、
  `plugins/__init__.py`）：补齐懒加载模块的显式类型导入，修复命令注册表的
  “not callable” 级联问题，恢复可调用性与 IDE 提示。
- **权限路径环防护**（`permissions/checker.py`）：修复变量遮蔽导致的
  类型不匹配。
- **WebUI 路径环逃逸**（`web/security.py:resolve_within`）：改用
  `os.path.realpath(strict=True)` 探测符号链接环（`ELOOP`），修复
  `strict=False` 静默截断导致环可绕过目录白名单的问题；新建文件仍正常回退。
- **WebUI 选择菜单契约**（`web/run_manager.py`）：`RunManager.options` 内部
  改存普通 dict，仅在 API 状态边界转换为 `SelectOption`，修复测试契约不一致。

### Changed

- **依赖审计**：`pyproject.toml` 增加 `mypy_path = "src"` +
  `explicit_package_bases`，使 mypy 从 `src/` 解析包，避免命中 site-packages 的
  陈旧安装导致泛型类被判定为非泛型。

## [1.16.2] - 2026-08-28

### Added

- **Pure-automatic background self-update** (`update.py`, `cli.py`): on every
  `ih` startup a daemon thread silently checks the GitHub release feed for a
  newer version and installs it without any confirmation. Throttled to once per
  day by default (`ITERATE_AUTO_UPDATE_INTERVAL_HOURS`), skips CI environments,
  and can be disabled with `ITERATE_AUTO_UPDATE=0`. The npm wrapper applies the
  new wheel automatically on the next run once published.
- **`reasoning_effort` config** (`iterate/types.py`, `iterate/config_loader.py`,
  `engine/query_engine.py`, `api/openai_client.py`, `config/config.schema.json`):
  `low` / `medium` / `high` threaded through schema → loader → engine →
  OpenAI-compatible request body. `low` saves tokens on quick review rounds,
  `high` deepens critical passes; unset follows the provider default. Invalid
  values degrade to the provider default rather than raising.
- **Visual-attachment passthrough into reviews** (`iterate/review.py`,
  `iterate/__init__.py`, `tools/iterate_tools.py`):
  `iterate_review` `plan` now accepts `attachments` entries
  (`{path?, data?, media_type?, caption?}`); screenshots/mockups/failure
  repros are injected as a mandatory "ATTACHED VISUAL CONTEXT" clause into
  every dimension reviewer prompt via `reviewer_task_prompt`, and the plan
  serializes them (`plan_to_dict`). Entries with neither `path` nor `data`
  are defensively dropped.

### Fixed

- Auto-update tests were environment-dependent: inside CI (or any shell with
  `CI=true`) they were silently skipped, so the actual install path never got
  asserted. Normal-path cases now clear the CI-detect variables, making them
  hermetic on GitHub Actions and locally.

## [1.16.1] - 2026-08-28

### Changed

- **Type-annotation cleanup across the codebase** (`cli.py`, `ui/runtime.py`,
  `ui/backend_host.py`, `api/client.py`, `api/openai_client.py`,
  `plugins/loader.py`, `plugins/types.py`, `hooks/*`): resolved all mypy
  strict errors in the touched modules — parameter/return annotations,
  dict generic arguments, `SupportsStreamingMessages` protocol shape (async
  generator vs coroutine), isinstance narrowing for dry-run preview data,
  and variable-shadowing fixes. No runtime behavior changes; tests unchanged
  (1977 passed).

## [1.16.0] - 2026-08-28

### Added

- **OrcaRouter gateway provider** (`api/registry.py`, `config/settings.py`,
  `cli.py`, `ui/backend_host.py`): built-in OpenAI-compatible gateway with
  `sk-orca-` key-prefix / base-url auto-detection, an `orcarouter` provider
  profile (default model `orcarouter/auto`, `ORCA_KEY` env var), a CLI setup
  menu entry and model-family guidance.
- **Signup shortcut in the API-key flow** (`auth/flows.py`): when a provider
  profile carries a `signup_url`, pressing Enter at the key prompt opens the
  signup page in the default browser and re-prompts — OrcaRouter's profile is
  wired to the project referral link, so first-time users can register for a
  free key without leaving the terminal. Onboarding guidance points to
  `ih setup orcarouter`.
- **Docs**: OrcaRouter section moved above Quick Start in `README.md` /
  `README.zh-CN.md`, highlighting the free-model tier and clarifying that only
  `ORCA_KEY` is required (base URL / model are baked into the profile).

### Fixed

- **OrcaRouter setup auth source consistency** (`cli.py`): the interactive
  `ih setup` OrcaRouter preset now stores the key under the `orcarouter_api_key`
  credential slot (`ORCA_KEY` domain) instead of the OpenAI slot, matching the
  built-in profile so `ORCA_KEY`-based auth works after guided setup.
- **Custom profile signup-url inheritance** (`config/settings.py`): a
  user-defined profile overriding a built-in one now inherits the built-in
  `signup_url` when it does not set its own, keeping the referral link active
  for customized OrcaRouter profiles.

## [1.15.0] - 2026-08-26

Feature release closing the seven functional gaps found in the design doc
(DESIGN-iterate-harness.md §11.2.2 / §18.4 / §18.5) and applying the fixes
for the 93 code-review findings across the web backend, iterate core and UI
layers. The iterate loop now knows when it is stalled or running out of
budget, risk-area writes are gated even in full-auto, and fix scope is
measured instead of assumed.

### Added

- **Stall-aware auto-pause** (`iterate/loop_policy.py`, P0, §18.5): new
  `iterate.stall_pause_rounds` knob (default `2`). After that many
  consecutive rounds with zero new findings the loop pauses and asks the
  user how to continue (`resume` / `skip` / `narrow` / `stop`) instead of
  silently declaring convergence. Setting it to `0` preserves the previous
  hard-stop-on-first-empty-round behaviour.
- **Budget-headroom pause** (`iterate/loop_policy.py`, P1, §18.5): new
  `iterate.budget_pause_min_rounds` knob (default `1`). When the remaining
  token/USD budget cannot cover that many rounds at the recent burn rate,
  the loop pauses and asks for a top-up or stop before the hard budget stop
  fires mid-round.
- **Risk-area approval gate** (`permissions/checker.py`, P1, §11.2.2): paths
  listed in `iterate.risk_area_paths` / `personalization.risk_areas`
  (accepting either a plain string or a `{path: ...}` object) now require
  explicit confirmation for every mutating tool call — even in `full_auto`
  mode. Read-only calls on those paths stay ungated. Wired into
  `build_permission_checker`.
- **Measured `is_atomic` check** (`iterate/fix_scope.py`, P2, §11.2.2): after
  each normal-mode round the actual change size is measured via
  `git diff --numstat`. When it exceeds `atomic.max_lines`, the finding is
  logged as a degraded-to-architectural decision entry and a steering hint is
  injected instead of relying on the sub-agent's self-report.
- **Deferred-fix state inheritance** (`iterate/checkpoint.py`,
  `iterate/decision_log.py`, `iterate/last_state.py`, P2, §11.2.2): the
  checkpoint now persists the deferred architectural-fix list
  (`extract_deferred_architectural`), the resume summary surfaces it, and
  `resume_kickoff` re-injects it across sessions so deliberately-untouched
  architectural work is not lost.
- **WebUI quick phrases** (`frontend/web`, P2, §18.4): one-click nudge chips
  in the input bar send steering phrases through the nudge channel, plus a
  stop chip that routes through the destructive-action confirmation dialog.
- **Frontend unit tests** (`frontend/web`, P2, §17.9): vitest 3.2.4 with 13
  tests covering `triageKey` generation, run-state/progress event mapping,
  chat-message deduplication and toast lifecycle.

### Fixed

- **Concurrency & state defects** (`web/run_manager.py`, `web/token.py`,
  `web/events.py`): double `resume`/`stop` no longer races on a finished
  asyncio future (`InvalidStateError`), the run-manager `_stopping` flag is
  no longer shared across concurrent tasks, start payloads are pre-validated
  before launch, token issuance is serialized with a lock, and the periodic
  status push uses a time-based snapshot check so the SSE stream cannot
  starve it.
- **Findings triage** (`web/findings_triage.py`): record/clear are now
  serialized with an exclusive file lock and write failures surface as
  errors instead of being silently swallowed.
- **Data integrity** (`iterate/checkpoint.py`, `web/routes/runs.py`,
  `web/routes/checkpoints.py`, `iterate/trend_store.py`): malformed round
  numbers degrade defensively, cross-round severity changes are preserved by
  filtering before dedup, and trend/checkpoint writes use atomic temp-file
  writes to avoid torn files on crash.
- **Review & last-run accuracy** (`iterate/review.py`, `iterate/last_state.py`,
  `iterate/ci_report.py`): round-index convergence is corrected, preview
  truncation no longer distorts finding counts, and workflow-command
  properties are comma-separated as GitHub expects.
- **Security hardening** (`web/security.py`): a symlink-loop in path checks
  now raises `ValueError` instead of leaking `RuntimeError`.
- **Config parsing** (`config/`): nested sections that are unexpectedly
  non-dict parse defensively; bundled config schema is kept in sync and the
  `go` command whitelist is widened.
- **CLI/installer ergonomics**: global CLI flags may now follow the
  subcommand, the full reviewer config is preserved, the npm installer
  rejects flag-like `--ai/--target/--token` values, and the interactive
  arrow-menu no longer stacks rows in raw terminal mode.
- **Web/terminal UX** (`frontend/web`, `frontend/terminal`): the nudge-clear
  button no longer dead-ends, the live transcript tab has a proper empty
  state, the triage key captures the right location, and the observatory
  clipboard-write fallback reveals the raw command text on failure.
- **Release tooling**: `workflow_dispatch` can select an explicit target tag,
  and the SkillHub badge link in the README points at the correct repo.

## [1.14.0] - 2026-08-22

Minor release adding the previously documented but unwired unattended CLI
commands and fixing two WebUI presentation issues. The automation entry
points now have first-class `ih` subcommands instead of existing only as
library functions.

### Added

- **`ih iterate schedule add|remove|status`** (`cli.py`): register (upsert),
  remove, or inspect the unattended quick-review cron job. Validates the
  5-field cron expression, `dry-run|normal` mode, and IANA timezone; exit
  codes and errors surface through typer.
- **`ih iterate hook install|uninstall|status`** (`cli.py`): managed
  pre-commit gate setup/teardown/inspection. Refuses to overwrite foreign
  hooks and degrades gracefully outside a git repo.
- **`ih iterate cron start|stop|status|history`** (`cli.py`): background cron
  scheduler daemon lifecycle and run history, with `--json` output.
- CLI unit coverage (`tests/test_commands/test_cli.py`) for all three command
  groups: happy paths, input validation, and non-git degradation.

### Fixed

- **Workspaces page** (`frontend/web`): a failed worktree removal no longer
  flips the whole page into the misleading "加载失败" empty state — the error
  now stays in a local, dismissible alert banner while the intact list
  remains visible.
- **Dashboard report size** (`frontend/web`): report size now uses the shared
  human-readable `formatSize` (KB/MB) instead of raw bytes, matching the
  Reports page.

## [1.13.1] - 2026-08-22

Patch release fixing report-finding reads across the three legacy
decision-log shapes. The decision logger records report findings under
different keys across versions (full `findings`, trimmed
`topFindings`/`notableFindings`, or a nested `summary` object), and the
consumers only looked at `findings` + `topFindings`. As a result the WebUI
last-run summary and `ih iterate report` could show 0 findings whenever the
newest report entry used another shape.

### Fixed

- **Unified report-finding extraction** (`iterate/decision_log.py`): new
  `findings_from_report` helper accepts every historical layout (full list,
  `topFindings`, `notableFindings`, or nested `summary`), so both the WebUI
  last-run summary and `ih iterate report` produce consistent, non-empty
  results.
- **`latest_report_entry` skips bare report entries** (`iterate/ci_report.py`):
  a `report` entry carrying only `verdict` (no findings/summary/count) is
  treated as "no report yet" instead of rendering a misleadingly-empty report
  with a passing exit code.
- **Consumer reads routed through the shared helper** (`iterate/last_state.py`,
  `iterate/ci_report.py`) with an explicit numeric count preferred from the
  entry or its nested `summary` before falling back to the list length.
- Added regression tests covering all legacy report shapes in
  `tests/test_iterate/test_decision_log.py`.

## [1.13.0] - 2026-08-22

Minor release to keep the iterate-harness distribution in step with the
released plugin line (iterate-plugin 2.11.0). Aligns the npm wrapper and the
Python package to the latest reviewed/hardened source state after the full
project review; no breaking API changes.

### Changed

- Version bumped to 1.13.0 across `src/iterate_harness/__init__.py` and
  `npm/package.json` (single source of truth retained in `__version__`).

## [1.12.12] - 2026-08-21

WebUI operation-console controls, sandbox hardening for file tools, and a
batch of UX fixes from the full project review.

### Added

- **Dashboard run controls** (`frontend/web/src/pages/Dashboard.tsx`): the
  persistent run-status banner on the dashboard now exposes direct
  pause / resume / stop buttons backed by `POST /chat/control`, so the
  operation console no longer depends on the chat panel. Stop requires a
  secondary confirmation and is disabled while the loop is waiting for human
  input.

### Security

- **Sandbox path validation for `todo_write` and `notebook_edit`**
  (`tools/todo_write_tool.py`, `tools/notebook_edit_tool.py`): both tools now
  resolve candidate paths and validate them against the Docker sandbox
  boundary the same way `file_write` already did, blocking directory-traversal
  escape attempts. Covered by new rejection tests in `tests/test_core_tools.py`.
- **No more hardcoded API keys in tests**: four real-API test files read
  `ANTHROPIC_API_KEY` from the environment (empty default) and skip their
  real-API cases when the key or the target workspace is absent.

### Fixed

- **Runs timeline diff expansion** (`frontend/web/src/pages/Runs.tsx`): the
  expandable diff block now normalizes both string and array persisted diff
  shapes, so it renders instead of being silently hidden.
- **StartDialog resume mode** (`frontend/web/src/components/StartDialog.tsx`):
  the "changed files / git ref" options are hidden in resume mode and a stale
  `changed` flag is cleared, preventing a bad kickoff.
- **Headless run guidance** (`cli.py`): after a `--headless` run finishes, the
  CLI prints next-step hints for viewing the HTML report or decision log.

## [1.12.11] - 2026-08-21

Security hardening, MCP 2.0.0 migration, and reproducible dependency pinning.

### Security

- **Credential encryption at rest** (`auth/storage.py`): when no system keyring
  is available (containers, CI, WSL), credentials in `credentials.json` are now
  encrypted with Fernet (authenticated symmetric encryption). The key is derived
  deterministically from the machine secret + user home + app salt, so the file
  is only decryptable on the same machine under the same user. Legacy plaintext
  values from older versions are migrated in place automatically.
- **WebUI access-token protection** (`web/token.py`, new): `ih web serve` issues
  a random token (persisted under the config dir with mode 600), prints it on
  startup, and injects it into the browser URL. All API calls must carry the
  token (`Authorization` header or `?token=` query), preventing local
  cross-process access to the loopback console.

### Changed

- **MCP 2.0.0 migration** (`mcp/client.py` + fixtures/tests): adapted to
  breaking changes — `FastMCP` → `MCPServer`, `inputSchema` → `input_schema`,
  `structuredContent` → `structured_content`, and the streamable HTTP client's
  new 2-tuple return. The HTTP transport now uses the dedicated `httpx2` client
  that MCP 2.0.0 requires.
- **Dependencies pinned to exact versions** (`pyproject.toml`): all runtime and
  dev dependencies are now exact-pinned (resolved from `uv.lock`) for
  reproducible installs. Added `httpx2==2.10.0`; upgraded `fastapi` to 0.141.1
  to resolve the `starlette` version conflict with MCP 2.0.0.
- **No more silent `except: pass`**: cleanup/best-effort error paths across
  checkpoint, report server, docker backend, cron scheduler, session storage,
  swarm, and task manager now log the reason at debug level instead of
  swallowing exceptions.

### Added

- **Test suites** for `state/`, `keybindings/`, `themes/`, WebUI token auth,
  and the auth manager (closing the coverage gaps identified in the full
  project review).

### Fixed

- **Unused imports** (`commands/registry.py`, two test files) removed so the
  pinned `ruff` rule set is fully green.

## [1.12.10] - 2026-08-20

Install source switched to the official **PyPI** index, so npm installs work
without GitHub in network-restricted (or TLS-broken) environments — exactly
the "no GitHub dependency" experience you get from DNS-fixed proxies, but
built in.

### Changed

- **Install channel** (`npm/lib/bootstrap.js`): the wrapper now installs the
  PyPI spec `iterate-harness==<version>` first, which pip resolves against the
  user's configured index/mirror (e.g. `pypi.tuna.tsinghua.edu.cn`). The
  GitHub pre-built wheel is demoted to a fallback (kept for users without a
  PyPI mirror), and the source archive is the final last-resort. Replaced the
  `installUrl`/`installFallbackUrl`/`fallback` API with an ordered
  `installCandidates` chain; HTTP candidates still get the node/curl
  local-download resilience, non-URL (PyPI) candidates are pip-installed
  directly.
- **Release distribution** (`.github/workflows/release.yml`): the released
  wheel is now also published to PyPI via twine (best-effort, gated on the
  `PYPI_PASSWORD` secret).
- **Tests** (`npm/test/bootstrap.test.js`): added `pypiInstallSpec`/
  `installCandidates` coverage and rewrote the `installHarness` suite for the
  new candidate-chain API (PyPI → wheel → archive), plus an end-to-end walk
  test asserting the exact pip/download call order.

## [1.12.9] - 2026-08-20

Interactive installer experience, mirroring iterate-skill-installer's banner + wizard.

### Added

- **Terminal UI helpers** (`npm/lib/ui.js`, new): `ITERATE` ASCII banner (cyan), colored `step/success/warning/error/info/hint` prefixes, a `frameSection` summary box, and an `askYesNo` interactive prompt — all written to **stderr**, so piped stdout stays clean.
- **Banner on every run** (`npm/lib/bootstrap.js`): `runHarness` prints the banner each time `ih` runs (skipped automatically when stderr is not a TTY, e.g. `ih --version | jq`).
- **Interactive install wizard** (`npm/lib/bootstrap.js`): on the first run, after `EnsureRuntime` detects the harness isn't installed, it asks `Install iterate-harness vX.Y.Z ...? [Y/n]` before downloading, then prints an "Installing" box with the version/runtime, and a "Done" box with getting-started tips. Declining the prompt exits cleanly (code 0) with a `CancelledError` message instead of erroring.
- **UI tests** (`npm/test/ui.test.js`, new): covers the banner shape, `stripAnsi`, `frameSection` alignment, and `CancelledError`.

## [1.12.8] - 2026-08-20

`ih` is now installed during `npm install` itself, not lazily on the first run.

### Added

- **Install-during-`npm install`** (`npm/scripts/postinstall.js`, new): an npm `postinstall` hook now creates the managed venv and pip-installs the harness right after `npm install`/`npm install -g` finishes — `ih` is ready immediately instead of waiting for the first run. It reuses the exact same `ensureRuntime` logic as the CLI entry point, never fails the npm install (it degrades to a runtime install on the first `ih` run when Python/network is unavailable or `ITERATE_HARNESS_SKIP_INSTALL=1`).
- **postinstall tests** (`npm/test/postinstall.test.js`, new): covers the `SKIP_INSTALL` fast path, a successful bootstrap delegation, and the bootstrap module smoke load.

## [1.12.7] - 2026-08-20

npm first-run install hardened for broken-Python-TLS environments: when pip can't fetch the release wheel (e.g. macOS python.org build with a broken certificate chain) and the wrapper downloads the wheel itself via Node/curl, the locally cached wheel now keeps a valid PEP 427 filename (`iterate_harness-<version>-py3-none-any.whl`) so pip accepts it instead of rejecting a bare `iterate-harness-<version>.whl`.

### Fixed

- **npm wrapper wheel cache** (`npm/lib/bootstrap.js`): `downloadCachePath` now returns the real wheel asset name for the `.whl` extension instead of a generic `iterate-harness-<version>.whl`. Previously, in the fallback path (Node/curl download), the cached wheel could not be pip-installed because pip rejects any `*.whl` lacking the `{python}-{abi}-{platform}` tags with "not a valid wheel filename".
- **Regression test** (`npm/test/bootstrap.test.js`): adds coverage asserting the wheel cache path is a valid PEP 427 wheel filename and round-trips through `artifactExtensionFor(wheelAssetUrl(...))`.

## [1.12.6] - 2026-08-20

npm first-run install fixed: the wrapper no longer tries to build the harness from the GitHub source archive (which lacks `frontend/web/dist`, so pip's source build always failed). It now downloads the **pre-built wheel** uploaded to the GitHub release and pip-installs that — mirroring how `iterate-skill-installer` ships pre-wrapped assets — with the source archive demoted to a last-resort fallback.

### Changed

- **npm wrapper install source** (`npm/lib/bootstrap.js`): `installUrl` defaults to the pre-built release wheel `iterate_harness-<version>-py3-none-any.whl` instead of the source archive. New `wheelAssetUrl` / `wheelAssetName` helpers build the pinned asset URL; new `installHarness` fallback path `pip from wheel URL → Node/curl download + local pip → (missing wheel) source archive`. New `installFallbackUrl` returns the archive URL only when the user did not override `ITERATE_HARNESS_INSTALL_URL`. Cache files keep the correct `.whl` / `.tar.gz` extension (`artifactExtensionFor`, `downloadCachePath` ext param).
- **Release distribution** (`.github/workflows/release.yml`, new): on a published harness release, installs frontend deps, builds `frontend/web/dist`, runs `uv build --wheel`, and uploads the wheel to the release. Ordering note: `npm publish` must wait for this job so the wrapper can fetch the wheel.

### Docs

- `npm/README.md` / `npm/README.zh-CN.md`: first-run step 3 and the `ITERATE_HARNESS_INSTALL_URL` table row now describe the wheel-first install with the source archive as fallback.

## [1.12.5] - 2026-08-20

Provider refresh + self-update + WebUI UX review: replaced ModelScope with OpenAI as a first-class provider, added an automatic update mechanism with an `ih update` command, and fixed a set of WebUI UX issues found in a full frontend pass.

### Added

- **OpenAI provider** (`api/registry.py`, `config/settings.py`): ModelScope removed from the provider registry and built-in profiles; OpenAI added as a standard provider with `OPENAI_API_KEY` env source, official base URL `https://api.openai.com/v1`, `gpt-5.4` default model, and the `gpt-5.4` / `gpt-5` / `gpt-4.1` / `o4-mini` model family for the `/model` picker; `ih auth login openai` and `ih setup` now offer OpenAI as a first-class choice.
- **Self-update** (`update.py`, `cli.py`): new `ih update` command with `--check` (report without applying), `-y/--yes` (skip confirmation) and `--force` (reinstall even when current) options. Detects the install layout (npm-managed venv / source checkout / plain pip), discovers the latest release from the GitHub feed (with a raw-file fallback), applies the matching update, and verifies the live version afterwards. Version discovery falls back to the system `curl` when Python's CA store is broken or stale (`SSL: CERTIFICATE_VERIFY_FAILED`), matching the installer's strategy for machines behind corporate proxies / self-signed roots.
- **Version-change notification**: `ih --version` now prints a one-line advisory hint when a newer release exists (cached for 24h, 3s timeout, never fatal; opt out with `ITERATE_HARNESS_UPDATE_CHECK=0`).

### Fixed

- **WebUI Runs page** (`frontend/web/src/pages/Runs.tsx`): changing the timeline type filter no longer keeps the stale pagination offset — the page resets to the newest page so a filtered result can never land on an empty later page.
- **WebUI report preview sandbox** (`frontend/web/src/pages/Reports.tsx`): the preview iframe now runs with `sandbox="allow-scripts"` only; the previous `allow-same-origin allow-scripts` combination would have silently voided the sandbox and given report-derived HTML same-origin access to the WebUI API.
- **WebUI dashboard layout** (`frontend/web/src/pages/Dashboard.tsx`, `styles.css`): the recent-reports / recent-audit pair moved off an inline fixed two-column grid onto a `.panels-grid` class that stacks to a single column on narrow viewports.

## [1.12.4] - 2026-08-20

User-facing copy review sweep: verified CLI/TUI help, READMEs, report output, bundled skills, frontend and install scripts are iterate-only, and removed the last autopilot vestige left by the 1.12.2 subsystem removal.

### Removed

- **Vestigial "Active Repo Context" injection** (`config/paths.py`, `prompts/context.py`, `tests/test_prompts/test_claudemd.py`): dropped `get_project_autopilot_dir` / `get_project_active_repo_context_path` and the runtime system-prompt entry for `.iterate-harness/autopilot/active_repo_context.md` — nothing in the codebase ever writes that file, so the read-side injection was dead code left behind when the autopilot subsystem was removed.

### Verified clean (no change needed)

- `ih --help` / `ih iterate --help` and the TUI slash-command registry: no stale commands or legacy wording.
- README / README.zh-CN, report text/CSV output, bundled skills, React TUI + web frontends, install scripts: no `ohmo` / `openclaw` / `openharness` / Slack / Feishu / Lark / webhook remnants.

## [1.12.3] - 2026-08-20

Final legacy sweep: removed the remaining external-communication surface and confirmed dead code so `ih` is purely the iterate review/fix harness with no channel/notification leftover.

### Removed

- **Webhook notifications** (`iterate/webhook.py`, `tests/test_iterate/test_webhook.py`, `cli.py`): dropped the Slack / Lark / Feishu / generic webhook notifier and the `ih iterate report --webhook <url>` option; exports (`WebhookResult`, `detect_webhook_type`, `notify_report`, `send_webhook`) removed from `iterate/__init__.py`.
- **Compatibility helpers** (`utils/helpers.py`, `tests/test_utils/test_helpers.py`): removed the unused `get_data_path` / `safe_filename` / `split_message` shims kept only for legacy channel adapters.
- **Empty OAuth service dir** (`services/oauth/`): removed the leftover empty package from the deprecated subscription/OAuth flow.
- **Dead channel config models** (`config/schema.py`): removed the unused `Config` / `ChannelConfigs` / `SlackConfig` / `FeishuConfig` / etc. compatibility models — no production or test code imports `iterate_harness.config.schema`.

## [1.12.2] - 2026-08-19

Iterate-only cleanup: stripped the remaining OpenHarness branding and the general-purpose agent-harness surface area from the CLI/TUI, auth, commands, workflows, and docs so `ih` presents purely as the iterate review/fix harness.

### Removed

- **Non-iterate CLI commands** (`cli.py`): dropped `cron`, `autopilot`, `hooks`, `mcp`, `plugin`, `reload-plugins`, `ship` from `ih --help`; dry-run classification table updated.
- **TUI slash commands** (`commands/registry.py`): removed `/hooks`, `/mcp`, `/plugin`, `/reload-plugins`, `/autopilot`, `/ship`; cleaned "ohmo" wording from the remaining `/stop` help.
- **Autopilot subsystem**: the whole repo-autopilot stack is gone — `src/iterate_harness/autopilot/`, the project autopilot path helpers, the `autopilot-run-next.yml` / `autopilot-scan.yml` / `autopilot-pages.yml` workflows, `autopilot-dashboard/`, `docs/autopilot/`, and related tests.
- **Redundant `local` provider profile** (`config/settings.py`, `cli.py`, `auth/manager.py`): removed the duplicate OpenAI-compatible "local" profile; Ollama now covers local endpoints, and `ih auth login` / `ih setup` no longer present a dead-end local provider.
- **Legacy release notes** (`RELEASE_NOTES_v0.1.8.md`, `RELEASE_NOTES_v0.1.9.md`): stale OpenHarness-era files removed.
- **Bug-report template `oh` reference** (`.github/ISSUE_TEMPLATE/bug_report.yml`): repro placeholder now uses `ih`.

### Changed

- **Branding** (`scripts/install.sh`, `frontend/terminal/src/components/WelcomeBanner.tsx`, `config/schema.py`): "Oh my Harness" / `ohmo` / `openclaw` references removed; `bot_names` is now `["iterate_harness"]`; the install banner is iterate-branded.
- **Auth guidance** (`cli.py`): `ih auth login` for local providers points to `ih setup` instead of dead-ending; setup hints include Ollama.
- **Skill docs** (`skills/bundled/content/skill-creator.md`): private skills path changed from `~/.ohmo/skills` to `~/.iterate-harness/skills`.

## [1.12.1] - 2026-08-19

Force reviewers to open every file in their assigned review scope and surface a coverage gap hint.

### Added

- **Scope inventory + coverage** (`iterate/review_scope.py`): `collect_scope_files` gathers the sorted relative-path inventory for a `full` scope, `chunk_files` batches it by `reviewer.scope_chunk_size` (default 25) while keeping directory runs together, and `compute_coverage` scores self-reported reads against the assigned inventory.
- **Scope batching in plan** (`iterate/review.py`): `build_review_plan` splits a `full` scope into per-chunk reviewer tasks when `scope_files` is supplied; `changed-only` stays a single batch owning the full delta.
- **Coverage hint in meta-review** (`iterate/meta_review.py`): new prompt-informative `COVERAGE_GAP` (medium severity) when reviewers' self-reported `readFiles` don't cover their assigned scope; never flips the verdict.
- **Config switches** (`iterate/types.py`, `iterate/config_loader.py`): `reviewer.coverage_validation` (default `true`) and `reviewer.scope_chunk_size` (default `25`).

### Changed

- **Reviewer prompt** (`iterate/review.py`): injected mandatory `COVERAGE RULE` — reviewers must `read_file` EVERY file in the assigned inventory before judging and return a `readFiles` array listing every file actually opened.
- **Evidence binary detection** (`iterate/evidence.py`): files containing a NUL byte are treated as not line-addressable (binary payload), same as an out-of-range line.
- **Violation attribution** (`iterate/meta_review.py`): `EVIDENCE_VIOLATION` now reports the round that first surfaced the poisoned finding.
- **Non-contiguous round convergence** (`iterate/review.py`): `aggregate_rounds` sizes `findings_by_round` by the max round number (not `len(rounds)`), and `compute_convergence` reads the last present round by its reported round number.
- **Runtime wiring** (`tools/iterate_tools.py`): the plan tool collects scope files and batches them; the meta-review tool computes coverage and threads it through the final report; `IterateConfigTool` exposes `coverageValidation`/`scopeChunkSize`.

## [1.12.0] - 2026-08-19

Forced evidence-based review: sub-agents must actually read files before judging, and fabricated locations fail the run.

### Added

- **Hard code-evidence gate** (`iterate/evidence.py`): new module validating every review finding's `file`/`line` against real files on disk (file existence, line bounds, whole-file/`0` lines, optional read-set cross-check, traversal-path rejection).
- **Evidence folding into meta-review** (`iterate/meta_review.py`): `build_final_review_report` now folds evidence results in — any fabricated path or out-of-range line becomes a critical `EVIDENCE_VIOLATION` and flips the verdict to `revise`.
- **Config switch** (`iterate/types.py`, `iterate/config_loader.py`): new `reviewer.evidence_validation` (default `true`); disabled via `false`.

### Changed

- **Reviewer prompt** (`iterate/review.py`): injected mandatory `EVIDENCE RULE` — reviewers must `read_file` every file they report on before judging, never report locations they did not read, and treat fabricated line numbers as poisoned evidence. `line` is now required for anchored, line-targeted findings (0 = whole-file/module-level).
- **Runtime wiring** (`tools/iterate_tools.py`): the meta-review path now runs the evidence audit against the real repo and threads the result through the final report.

### Security

- Evidence gate prevents hallucinated file paths / invented line numbers from entering the report as credible issues.

### Tests

- Added evidence module tests (existence, line bounds, whole-file, read-set, traversal) and meta-review evidence folding tests; full suite passes.

## [1.11.3] - 2026-08-18

Full implementation audit (code + UX) closing all remaining gaps with tests; worktree metadata no longer pollutes git state.

### Fixed

- **Worktree metadata polluted the worktree's git state** (`swarm/worktree.py`): the ownership JSON written inside each worktree made directories appear modified to git, so a subsequent `git add -A` in the fix flow could stage the metadata or fail an "empty diff" check. Metadata is now persisted as a sidecar `<ns>/<flat_slug>.meta.json` *outside* the worktree, keeping the worktree's git state clean while preserving ownership/agent/slug lookups (with regression tests).
- **Streaming STT placeholder** (`voice/stream_stt.py`): replaced a silent placeholder that parsed real audio into non-speech frames with an explicit `NotImplementedError` so unsupported engines fail loudly instead of degrading silently.
- **Settings file permissions** (`config/settings.py`): the persisted settings file is now written `0o600` (owner-only) instead of default permissive mode, protecting any locally cached secrets.
- **Swarm lifecycle hardening** (`swarm/registry.py`, `swarm/team_lifecycle.py`): ownership/teardown flows now surface and log errors instead of swallowing them (empty exception blocks removed), keeping resource cleanup reliable.
- **Worktree spool hardening** (`swarm/spawn_utils.py`, `swarm/in_process.py`): input validation and error propagation tightened for agent spawn/spool execution.
- **Report checksum verification** (`iterate/checkpoint.py`, `iterate/review.py`): checkpoint/review artifact handling hardens against corrupt or missing sidecar data.
- **API secret exposure** (`web/routes/config.py`, `api/*`): API-key style fields are masked (not truncated) in config responses so they cannot be partially leaked to the UI.
- **Bridge secret encryption** (`bridge/work_secret.py`, `bridge/manager.py`): secret-at-rest encryption is enforced and decryption failures are logged explicitly.
- **WebUI page states** (frontend `web/src/pages/*`): Runs/Dashboard/Checkpoints/Reports/Workspaces now render distinct loading/empty/error states instead of silently blank — including a visible failure state when run `find`-results fail to load (previously an unhandled promise could leave the list blank).
- **Type coverage** (frontend `web/src/types.ts`): completed type definitions so the frontend builds cleanly under strict checks.

### Added

- **Voice test suite** (`tests/test_voice/`): unit tests for `stream_stt` and `voice_mode`.

## [1.11.2] - 2026-08-18

WebUI reliability fixes from a full implementation audit.

### Fixed

- **Chat history lost after SSE disconnect/reconnect** (`store.ts`): the hub only serves live subscribers, so chat messages, progress events and decision-log entries published while the stream was down were permanently lost on reconnect. `onopen` now resyncs chat history + run status from REST and bumps the decision-log revision on (re)connect — the UI never shows a stale transcript.
- **Assistant output dropped on early run termination** (`web/run_manager.py`): buffer text was only flushed on an `AssistantTurnComplete`, so a turn interrupted by an error, an early stop or a system-exit silently discarded the model's partial output. A `_flush_assistant_buffer` in the run loop's `finally` now publishes any buffered text (no-op when the last turn was already flushed), plus a regression test.
- **Report mtime conversion wrapped in an empty exception** (`web/routes/reports.py`): conversion failures were swallowed with `except Exception: pass`, hiding the error and leaving `modified` silently null. Replaced with an explicit `_to_modified_iso` helper that logs a warning and degrades to `None` only for genuinely un-representable mtimes, with tests.

## [1.11.1] - 2026-08-17

WebUI review fix: truthful tool-failure markers in the human-in-the-loop chat timeline.

### Fixed

- **Tool failure shown as success** (`web/run_manager.py`): `ToolExecutionCompleted` carries an `is_error` flag but the chat stream ignored it, so a failing tool call still rendered a green `✔` checkmark in the live tool timeline — misleading during model laziness / fault diagnosis. The manager now publishes `✖ {tool}：{preview}` for failing calls, keeping the tool timeline truthful.
- **Frontend tool-card failure state** (`components/ChatPanel.tsx`, `styles.css`): the tool-line parser now distinguishes `✔/✖` into done / failed states; failed cards render with a red accent and a "失败" label (the nested status ternary was also refactored into a lookup table).
- **Version sync**: `npm/package.json` was left at `1.10.1` after the 1.11.0 release — bumped to `1.11.1` to restore the release-required lock-step (`npm == harness == tag`).

## [1.11.0] - 2026-08-17

WebUI iteration wave: workspaces management, findings triage, tool-call visualization, and UX robustness for the management console (design §17.3 P2/P4, §18). Focus stays on the *run* — the new surfaces complement the dashboard/timeline/checkpoints and the human-in-the-loop chat panel.

### Added

- **Findings triage journal** (`web/findings_triage.py`, `web/routes/runs.py` `GET/POST/DELETE /runs/findings/triage`): persistent approve/reject decisions in `.iterate/findings-triage.jsonl` (append-only, latest decision wins), dedup keyed on `(file, line, dimension)`, all mutations require `confirm=true` and are audit-logged. The Runs page (`pages/Runs.tsx`) adds approve / reject buttons per finding, a triaged-state filter, and a "clear all decisions" action with a secondary confirmation dialog.
- **Workspaces management** (`web/routes/workspaces.py`, `pages/Workspaces.tsx`): `/workspaces` lists the primary checkout plus every isolate worktree whose `original_path` matches the project (git metadata, config isolation flag, entry count); `/workspaces/remove` removes a stale worktree — slug validated against path traversal, `confirm=true` required, audited. New `/workspaces` route wired into `App.tsx`.
- **Tool-call visualization** (`components/ChatPanel.tsx`): tool-execution messages render as status cards (executing / done / idle) with tool name and detail, so the chat panel makes the model's tool activity legible at a glance.
- **UX robustness layer** (`components/ErrorBoundary.tsx`, `components/Skeleton.tsx`): page-level error boundary with retry / reload, skeleton loaders for tables and cards.
- **Keyboard shortcuts + store hardening** (`App.tsx`, `store.ts`): `g <key>` page jumps, `/` or `Cmd/Ctrl+K` toggles the chat panel; SSE disconnect triggers polling fallback, connection-state toasts, and browser notifications when the run waits on a human decision.

### Fixed

- **Frontend type-check regressions** (`src/api.ts`, `pages/Runs.tsx`): removed a duplicate `request` export and a `triage` identifier collision between the triage state map and the callback — `npm run typecheck` is clean again.
- **Workspaces remove button unreachable** (`web/routes/workspaces.py`, `pages/Workspaces.tsx`): the list filtered to project-owned worktrees while the frontend required `!active` to show the delete action, making deletion dead UI. Worktrees are now flagged `detail.stale` (round older than the project's latest round) and only stale ones expose the remove action, so the active round's sandbox is never deleted mid-run.
- **Triage key mismatch for findings without a line number** (`web/findings_triage.py`): the backend dedup key rendered a missing line as the literal `"None"` while the frontend rendered it as `""`, so persisted approve/reject decisions never matched their row after a reload and re-triaging duplicated records. `_key` now canonicalizes `None` → `""` to match the frontend exactly.
- **Non-atomic triage journal rewrite** (`web/findings_triage.py`): `_rewrite` now writes a temp file and atomically replaces the journal so an interrupted clear-all never truncates the journal.
- **Version sync**: `frontend/web/package.json` bumped to `1.11.0` to match the Python harness (was stuck at `1.10.0`).

## [1.10.1] - 2026-08-17

Code-quality / release-hygiene pass from a full implementation audit:

### Fixed
- **Single source of truth for the version**: `src/iterate_harness/__init__.py` now
  exposes `__version__` and `pyproject.toml` reads it at build time
  (`[tool.hatch.version].path`). The CLI (`cli.py`) and the WebUI FastAPI app
  (`web/api.py`) report `__version__` instead of duplicating the literal, so a
  version bump can never drift across files.
- **npm wrapper version drift**: `npm/package.json` was stuck at `1.9.4` while the
  Python harness moved to `1.9.5`/`1.10.x`. Bumped to `1.10.1` to restore the
  release-required lock-step (`npm == harness == tag`).
- **Observability of swallowed errors**: `coordinator/agent_definitions.py` now logs
  malformed agent-file parse failures at WARNING (was DEBUG) so config mistakes
  surface; `iterate/onboard_cmd.py` logs the auth pre-check fallback at DEBUG
  instead of an opaque `pass`.
- **Comment fix** in `utils/shell.py` (clarified the non-Docker code path label).

## [1.10.0] - 2026-08-17

Ships the full WebUI management console (design §17) with the iterate-specific conversational control panel (design §18). The WebUI is now the primary operating surface: users start, monitor, pause and resume iterate loops from the browser, and confirm decisions / nudge a stalled loop through a side chat panel. Per design §18, the harness center stays the *run*, not the conversation — the chat panel is a human-in-the-loop instrument for decision confirmation, status updates, and urging the model when it stalls mid-round.

### Added

- **WebUI management console** (`web/api.py`, `web/routes/{status,runs,checkpoints,config,reports}.py`, `frontend/web`): FastAPI backend + React frontend with Dashboard / Runs timeline / Checkpoints / Budget & rate / Config / Reports pages, dark mode, SSE real-time push, timeline pagination, and a "启动迭代" start entry point.
- **In-process event hub** (`web/hub.py`): pub/sub fan-out (`ChatHub`) bridging the iterate run loop to every SSE connection; full queues drop the oldest event so the run loop never blocks on a slow client.
- **Live run manager** (`web/run_manager.py`): owns the single in-server iterate loop, substitutes the engine's three human-interaction channels (`permission_prompt` / `ask_user_prompt` / `ask_user_select`) with Web versions that pause the run and await the answer, and persists a human-interaction-only transcript to `.iterate/web-chat.jsonl`.
- **Human-in-the-loop nudge injection** (`iterate/loop_policy.py` `IterateLoopPolicy.inject_nudge`): user 督促 messages are queued and prepended to the next-round instruction at the round boundary, so a stalled model is urged to continue even mid-loop.
- **Conversational control panel** (`web/routes/chat.py`, `frontend/web/src/components/{ChatPanel,RunStatusCard,StartDialog}.tsx`): chat routes (`/chat/start`, `/chat/status`, `/chat/message`, `/chat/control`, `/chat/history`) plus a fixed overlay panel that auto-opens when a decision is pending and supports permission approval, select/prompt answers, pause/resume/stop, and free-text nudges.
- **Security posture** (`web/security.py`): loopback-only binding by default, strict CORS, path whitelisting against traversal, API-key redaction, and append-only audit log for the WebUI surface.

## [1.9.4] - 2026-08-17

Implements the remaining 28-item review backlog left open by the 1.9.3 feature wave (design §15.4): triage results now persist into `iterate.config.yaml`, prompt template presets, multilingual + CSV reports, offline model providers, run-to-run diffing, branch-targeted review, and webhook push notifications.

### Added

- **Triage → config bridge** (`iterate/personalization.py` `sync_known_intentional_to_config`, wired in `tools/iterate_tools.py` `_persist_ignores`): entries marked "always ignore" during `iterate_triage` now merge into `personalization.known_intentional` of the project `iterate.config.yaml` (deduped, preserving hand-written entries, atomic write, `managed-by-iterate-triage` marker) so the whole project — not just the harness's private JSON — sees the filters from the next round.
- **Prompt template presets** (`iterate/prompts.py`): `TEMPLATE_PRESETS` registry with `standard` (default), `strict` (conservative, safety-first) and `quick` (impact-only) presets; `template_suffix()` normalizes mode (`dry-run` → `dry_run`); CLI `iterate review|run --template <preset>`.
- **Multilingual reports** (`iterate/ci_report.py`): `L10N_TEXTS` (en/zh) with header / gate / no-findings strings; `render_text(..., language=)`; CLI `--lang en|zh` (default `en`).
- **CSV export** (`iterate/ci_report.py` `render_csv`): findings written as UTF-8-BOM CSV (severity/dimension/file/line/summary/failure_scenario/suggested_fix) for direct Excel opening; CLI `iterate report --csv <path>` (`-` → `.iterate/report.csv`).
- **Offline model providers** (`config/settings.py`): `local` (openai-compatible localhost, e.g. llama.cpp / LM Studio / vLLM) and `ollama` provider profiles with `auth_source: local`; unblock BYOK-style workflows against local endpoints.
- **Run-to-run diff** (`iterate/trend_store.py`): `diff_runs()` + `RunDiff` classify findings as new / fixed / regressed / unchanged by fingerprint (regressions identified via `previously_fixed_findings`); `render_diff()` human-readable summary.
- **Branch-targeted review** (`cli.py` `iterate review|run --branch`): review/loop runs against a target branch via worktree isolation; `commands/iterate.py` `/iterate dimensions` shows configured dimensions and their resource allocations (model / concurrency / token_budget).
- **Webhook notifications** (`iterate/webhook.py`): universal notifier auto-detecting Slack Incoming Webhook, Lark/Feishu custom bot and generic JSON endpoints; rich Slack Blocks / Feishu interactive card payloads with severity emoji; CLI `iterate report --webhook <url>`.
- **Common-failure self-healing guide** (`README.md` "Troubleshooting / 常见失败自愈指南"): TLS / auth / rate-limit / checkpoint / provider-not-found scenarios with concrete fixes.

### Fixed

- **`test_bash_tool` partial-output assertion** remains a known environment limitation (macOS PTY without the `script` wrapper cannot stream partial output through `create_shell_subprocess`); unrelated to this release and left for a follow-up cross-platform pass.

## [1.9.3] - 2026-08-16

Six high-value capability gaps identified in the post-1.9.2 UX review (design §15.3) are now implemented end to end: BYOK custom model providers, convergence dashboard in the TUI, failure recovery via checkpoints, session workspace isolation, budget enforcement / rate limiting, and an HTML report service with round replay.

### Added

- **Custom model provider (BYOK)** (`config/settings.py`): `ProviderProfile` (BaseModel) supports custom API format / auth source / `base_url` / default model. Ten built-in provider profiles (claude-api / claude-subscription / openai-compatible / codex / copilot / moonshot / gemini / minimax / nvidia / qwen / modelscope) plus `default_provider_profiles()` / `merged_profiles()` / `resolve_profile()`. `ih provider add` registers a custom endpoint; `auth/manager.py` loads credentials per profile; `/model` shows profile state.
- **Convergence dashboard in the TUI**: `ReviewProgressEvent` (round / new_findings / per_dimension / token_cost) dispatches through `ui/app.py` into a React `ReviewProgressPanel` — findings sparkline, per-dimension counts, cumulative cost.
- **Failure recovery / checkpoint resume** (`iterate/checkpoint.py`, `iterate/last_state.py`): atomic checkpoints (`save_checkpoint` / `load_checkpoint` / `clear_checkpoint` with file locking) persist the last successful round's state; the engine reloads it on resume so a model failure resumes from the last converged point instead of round 1.
- **Session workspace isolation** (`iterate/worktree_flow.py`, `iterate/worktree_runtime.py`, `swarm/worktree.py`): `WorktreeSession` (serializable) + git command wrapper create / merge / roll back a dedicated git worktree per round; the engine (`engine/query.py`) runs fix rounds inside the sandboxed worktree, merges on success, drops on failure. Includes per-repo namespace hashing and stale-worktree cleanup.
- **Budget enforcement / rate limiting** (`iterate/loop_policy.py`): `IterateLoopPolicy` gains `total_token_budget` (hard token cap → STOP with closing report guidance) and `budget_usd` (CostMeter-cumulative USD cap → STOP) via `_budget_stop_reason()`; `max_turns_per_minute` throttling (`RATE_LIMIT_WINDOW_SECONDS=60` rolling window + `_throttle_delay`) is consulted by the engine via `before_request`.
- **HTML report service** (`iterate/report_server.py`, `iterate/html_report.py`): static HTTP server (`serve_report`, oneshot/persist modes, auto-browser-open, MIME table) and an interactive round-replay page (`build_replay_page` — per-round panels, prev/next navigation, jump dots, keyboard ←/→, type-specific entry cards, HTML-escaped against XSS). Both CLI (`iterate report --serve/--serve-port/--serve-persist`) and TUI (`/iterate report --serve`) entry points.

### Fixed

- **`mcp` dependency pin** (`pyproject.toml`): narrowed `mcp>=1.0.0` to `mcp>=1.0.0,<2.0.0` — mcp 2.0 removed `mcp.server.fastmcp`, breaking the MCP HTTP integration tests. The upper bound keeps the FastMCP server API that the tests rely on.
- **Package `py.typed` marker** (`src/iterate_harness/py.typed`): the package previously shipped without a typed marker, so mypy treated it as an untyped third-party package and skipped analysis. The marker makes mypy type-check the harness source properly (new modules verified clean).

## [1.9.2] - 2026-08-16

Found during the post-1.9.1 full code review of the harness core (`iterate/` loop bookkeeping, `services/cron_scheduler`, `iterate/onboard_cmd`): four real data-integrity / robustness / portability defects, each with a regression test.

### Fixed

- **Decision log defensive parsing** (`iterate/decision_log.py`): `read_entries` raised `ValueError`/`TypeError` on malformed entries (non-numeric `round`, non-mapping `data`), crashing consumers like `report --fail-on` and trend analysis on a single bad line. Field parsing is now guarded; malformed lines are skipped with a warning instead of aborting the whole read. Tests: non-numeric round skipped, non-mapping data skipped, corrupt-file resilience.
- **Trend library key mismatch** (`iterate/trend_store.py`): `TrendRecord.to_dict()` serialized camelCase (`firstSeen`/`lastSeen`/`fixedAt`) while reads used snake_case, so cross-run trend classification (`new`/`fixed`/`regressed`/`stubborn`) silently misread persisted data after a restart. Serialization standardized on snake_case to match deserialization.
- **Onboarding config overwrite** (`iterate/onboard_cmd.py`): `run_onboard` (and thus `reonboard`) rebuilt `iterate.config.yaml` from scratch, dropping user-owned sections (personalization, review, budget, cron, …) whenever onboarding ran against an existing config. New fields now merge over the existing config (`_merge_into_existing`) instead of replacing it.
- **Cron scheduler daemon on Windows** (`services/cron_scheduler.py`): `start_daemon` used `os.fork()`/`os.setsid()`, which are Unix-only and crash on Windows. Daemon spawn now uses `subprocess.Popen` with a fully detached child, keeping the scheduler usable on all supported platforms.

### Reviewed (no change)

- **Pre-commit hook gate** (`iterate/git_hook.py`): the `|| exit 1` after `iterate review --changed --clean-ok` was flagged during review. Analysis: `review --changed` only exits non-zero on a genuine review failure (model/auth crash) or invalid ref; in that case failing the commit closed is the intended fail-closed behavior, and it does NOT bypass the `report --fail-on` severity gate (which runs whenever review exits 0). Removing it would make the hook fail-open on a crashed review — left unchanged.

## [1.9.1] - 2026-08-16

Distribution-only patch for the npm wrapper. Found during the 1.9.0 release e2e: on hosts where Python's TLS trust store is broken (typical for macOS python.org installs that never ran `Install Certificates.command`), `pip install <github tarball>` fails certificate verification while every other tool on the machine (curl, Node, browsers) downloads the same URL fine — the wrapper dead-ended with guidance text.

### Fixed

- **Tarball download fallback** (`harness/iterate-harness/npm`): when the pinned GitHub release tarball cannot be pip-installed, the wrapper now retries by downloading the tarball into `~/.iterate-harness-npm/cache/` and pip-installing the local file. The download itself is two-tiered: Node's own https stack first (redirect-following, 120s timeout), then `curl` (system trust store — survives intercepting proxies and Node's bundled-CA gaps; TLS verification stays ON in both tiers, partial files are cleaned up between attempts). Non-http targets (local path / `ITERATE_HARNESS_INSTALL_URL` overrides) keep the old single-attempt behavior; when everything fails, the error carries the original pip diagnosis plus every download-tier failure reason.
- **Entry points**: `ih` / `iterate-harness` bin shims now handle the async bootstrap (failures still exit 1 with the same actionable message, never a raw stack trace or unhandled rejection).

## [1.9.0] - 2026-08-16

Closes the v1.24 leftover: the 9-category personalize wizard is now fully usable inside the TUI — no terminal hop required. `/iterate personalize` opens a directional-key menu flow over the same channels the Esc intervention menu uses (select modal + question modal); headless sessions keep the summary + CLI pointer.

### Added

- **Directional-key personalize wizard** (`iterate_harness.iterate.personalize_tui`): `/iterate personalize` now runs an interactive 9-category wizard in the TUI. Main menu lists every category with live entry counts plus Save&finish / Cancel; each category editor supports add (text via the question modal) and per-entry removal; `known_intentional` collects file → dimension (select over the canonical 9) → line (invalid → 0) → reason; `dimension_focus` pairs the dimension select with a focus prompt; `fix_priority_order` reorders via move-to-front (any permutation reachable) plus reset-to-default; `extra_validation_commands` keeps the strict whitelist validation with the rejection reason surfaced in the re-prompt. A final confirm gate offers save / keep-editing / discard. The save chain is byte-identical to the CLI wizard (`save_personalization_to_config` + `update_iterate_md_user_section`); wizard mutations happen on a deep copy, so cancel discards cleanly.
- **`QueryEngine.ask_user_select_channel` / `ask_user_prompt_channel`**: read-only properties exposing the interactive channels to slash commands (both `None` in headless runs).

### Changed

- `/iterate personalize` without interactive channels (headless) still shows the personalization summary and points at `ih iterate personalize`; nothing changes for CLI users.

## [1.8.0] - 2026-08-15

Closes the last v1.22 v2 candidate: the skill↔harness dimension-system consistency check. The 9 review dimensions are defined in six places across the two repositories (canonical yaml, JSON-schema enum, skill wizard constants, harness `ALL_DIMENSIONS`, harness default config, and every project's config references) — any of them can drift during a rename. This release bundles the canonical definitions inside the harness and ships a `doctor` command that answers "are my dimensions consistent?" in one shot, plus a six-source lock test in the skill repository that fails CI on drift.

### Added

- **`ih iterate doctor` / `/iterate doctor`** (`iterate_harness.iterate.dimension_check`): dimension-system consistency check. Loads the bundled canonical `data/dimensions.yaml` (byte-identical to the skill's `config/dimensions.yaml`), verifies the harness-internal constants (`personalize_cmd.ALL_DIMENSIONS` order, `IterateConfig` default set) against it, then validates every dimension reference in the project's effective `iterate.config.yaml`: enabled `dimensions` (unknown key = error, e.g. a `securty` typo), `dimension_resources` / `thresholds.dimensions` keys (unknown = error; configured-but-not-enabled = inert warning), and `personalization` references (`fix_priority_order` / `dimension_focus` / `known_intentional` outside the enabled set = error, mirroring the skill's `scripts/validate.py` semantics). Canonical dimensions not enabled are reported as informational. CLI exits 1 on drift (CI-gateable); the TUI renders the same report.
- **Bundled canonical dimension data** (`iterate_harness/iterate/data/dimensions.yaml`): packaged in the wheel; `load_canonical_dimensions` parses it defensively (missing file / bad yaml / malformed entry / invalid priority are reported as errors, never raised).
- **Six-source dimension lock test** (skill repository, `tests/test_dimension_lock.py`): locks the canonical yaml against the JSON-schema enum, the skill wizard's `ALL_DIMENSIONS` + `DIMENSION_LABELS`, the harness `personalize_cmd` constants, the harness default-config dimensions, `init_wizard.BASE_DIMENSIONS` (subset), and byte-identity of the bundled copy. Extraction is AST/regex/json only — zero third-party dependencies.

### Changed

- **Ruff rule set pinned explicitly** (`[tool.ruff.lint] select = ["E4", "E7", "E9", "F"]`): ruff 0.16 expanded its default rule set, which would have failed the CI quality job on the next dependency resolve (uv.lock is not distributed). The pin keeps the lint scope identical to what the codebase was written against, independent of ruff version bumps.

### Fixed

- Removed an unused `prompts` import in the pause-handler path (`engine/query.py`) and an f-string without placeholders in onboarding output (`iterate/onboard_cmd.py`) — the only two violations under the pinned rule set.

## [1.7.0] - 2026-08-15

Closes the three v1.21 leftovers: giant-PR comment pagination, exact per-dimension USD billing, and per-dimension cost in the TUI convergence panel. Also removes the inherited OpenHarness logo from the READMEs (trademark hygiene for the fork).

### Added

- **Per-dimension USD in the convergence panel**: `ReviewProgressEvent` carries a `dimension_cost_usd` map end-to-end (loop policy → protocol → backend host → React panel). The panel now renders `security 3 ~$0.0210` style per-dimension estimates next to the counts whenever reviewer usage was reported; the header keeps the metered main-loop total.
- **Exact in/out split billing** (`dimension_usage_io`): the `iterate_review` aggregate operation accepts an optional per-dimension input/output split (e.g. `{"security": {"input": 1000, "output": 500}}`). Split dimensions bill at the model's exact input/output prices; bare-total dimensions keep the blended-average estimate. Split reports also feed dimension token-budget audits even without bare totals. All shapes are sanitized defensively — malformed entries drop, never raise.
- `CostMeter.record_dimension_usage(dimension, input_tokens, output_tokens)`: monotonic per-stream max accounting (same anti-double-count discipline as the bare totals).

### Changed

- **PR comment lookup paginates** (`pr_comment._find_marker_comment`): the marker scan now pages through the PR comment list (`per_page=100`, up to a 10-page defensive cap) instead of reading a single page — the idempotent update path keeps working on giant PRs with 100+ comments. Pages are chronological; the highest page's marker wins.
- README (en + zh-CN): removed the inherited OpenHarness logo image (`assets/logo.png` deleted — the fork no longer ships upstream branding); version badges updated to 1.7.0.

## [1.6.0] - 2026-08-15

Personalization parity with the skill: the full 9-category wizard, kernel-enforced protected paths, and fingerprint auto-capture after TUI onboarding.

### Added

- **`ih iterate personalize`** (`iterate_harness.iterate.personalize_cmd`): the skill's 9-category personalization wizard, re-run any time to edit in place (existing config + `ITERATE.md` are pre-loaded as defaults). Structured categories (protected paths, risk areas, known-intentional, dimension focus overrides, fix priority order, forbidden fixes) are written to the `personalization` section of `iterate.config.yaml`; free-text categories (iterate notes, code conventions) are rendered into the `ITERATE.md` user-owned region with exact-header section replacement — manually written user sections survive verbatim. Extra validation commands pass the skill's strict whitelist (forbidden shell metacharacters, allowed tool prefixes, `ITERATE_EXTRA_SAFE_PREFIXES` env extension) BEFORE merging into `validation.commands` + `command_whitelist`, so schema validation can never fail on a hostile entry. `/iterate personalize` in the TUI shows the current personalization summary and points at the CLI wizard.
- **Kernel-enforced protected paths**: `build_permission_checker` (`permissions/checker.py`) now also merges the current project's `personalization.protected_paths` into the deny path rules (normalized to absolute-path globs) — wizard-configured protected paths are a hard permission boundary, not just a prompt-side instruction. Unreadable configs contribute no rules and never crash permission bootstrap.
- **Personalization constraints in every kickoff**: review/run kickoffs append the personalization constraint block (protected paths, risk areas, forbidden fixes, fix priority, dimension focus, known-intentional count) so every loop starts with the project-specific rules in context.
- **Fingerprint auto-capture after TUI onboarding** (`ensure_onboarding_fingerprints`): when `ITERATE.md` exists but the config has no `onboarding.fingerprints` (the TUI `/iterate onboard` path — the slash-command flow cannot run the CLI's synchronous post-scan bookkeeping), the next review/run/drift-check completes the config's `onboarding` section with harness-serialized fingerprints. The model never touches trusted config; users no longer need a manual `ih iterate refresh` after TUI onboarding.
- Defensive config parsing: `load_personalization_from_config` accepts either a full config dict or the personalization section itself, and string lists keep only non-empty `str` entries (hostile YAML degrades to empty instead of coercing).

### Distribution

- **npm distribution wrapper** (`iterate-harness` on npm, maintained at `harness/iterate-harness/npm/` in the iterate-skill monorepo): `npm install -g iterate-harness` exposes `ih` everywhere npm works. The wrapper is a zero-dependency Node shim — first run resolves Python ≥ 3.10 (env override `ITERATE_HARNESS_PYTHON`), creates a managed venv (`~/.iterate-harness-npm`), pip-installs the release tarball **pinned to the npm package version** (lockstep: npm 1.6.0 → harness v1.6.0, npm upgrades self-heal on next run), then delegates to the real `ih` with argv/stdio/signals/exit-code passthrough. Requires network access to pypi.org + github.com for the one-time install; `ITERATE_HARNESS_SKIP_INSTALL=1` runs an existing install directly. 10 unit tests (`node --test`) cover the pure helpers.

### Changed

- README (en + zh-CN): `personalize` in the CLI cheat-sheet, a "Personalization wizard" feature row, the onboarding row now mentions TUI fingerprint auto-capture; version badges updated to 1.6.0.

## [1.5.0] - 2026-08-15

Full onboarding parity with the skill: model-driven `ITERATE.md` generation, manifest fingerprints, drift detection, and knowledge-base injection into every loop.

### Added

- **`ih iterate onboard`** (`iterate_harness.iterate.onboard_cmd`): the complete first-run flow the skill has — auth gate (`ih auth login` guidance, never a bare stack trace) → detection evidence (`init_wizard.detect_project`) → interactive dimension/goal/rounds Q&A (`--yes` skips) → **model-driven project scan**. The model explores the repo with its read tools (manifests, 2-3 level tree, specs/tests/CI, README — the kickoff embeds the skill's sensitive-file deny list `.env`/keys/credentials) and writes `ITERATE.md` using the bundled skeleton (`iterate/data/ITERATE.template.md`) with byte-exact region markers. Harness code then validates marker presence/order, captures SHA-256 manifest fingerprints (15 tracked manifests, skill parity), and serializes `iterate.config.yaml` via `yaml.safe_dump` — untrusted model prose never reaches trusted config structure. `--no-ai` renders a detection-only knowledge base (channel `cli`) without any model call; the AI path records channel `ai`. Both artifacts are byte-compatible with the skill's onboarding output (same markers, same `onboarding.fingerprints` schema) so either ecosystem can read what the other produced.
- **`ih iterate refresh`**: re-captures fingerprints, prints a drift summary (changed/added/removed manifests), updates the config `onboarding` section + the `ITERATE.md` metadata row with rollback on write failure. Never calls the model, never touches the user-owned region.
- **`ih iterate reonboard`**: backs up both artifacts (`*.bak-<timestamp>`), re-runs the full model onboarding with the existing user-owned region embedded verbatim in the kickoff, and restores the backups automatically when the re-scan fails validation.
- **`ih iterate status`** (new CLI subcommand): effective config summary + onboarding state (channel, completed_at, fingerprint count) + drift check result. The TUI `/iterate status` shows the same block.
- **Knowledge-base injection**: `build_runtime_system_prompt` now inlines the project's `ITERATE.md` (found by walking up from cwd, capped at 4000 chars with a pointer to the `iterate_context` tool for the full file) — every review/run kickoff starts with the project knowledge in context, closing the v1.22 gap.
- **Drift warnings**: `ih iterate review` / `run` / `resume` print a non-blocking warning when tracked manifests drifted since onboarding; the TUI `/iterate review|run` start messages carry the same notice.
- TUI `/iterate onboard`: submits the onboarding kickoff inside the current session (no nested runtime); the completion message points at `ih iterate refresh` to record fingerprints.

### Changed

- README (en + zh-CN): onboarding commands in the CLI cheat-sheet, an onboarding explainer paragraph (trust boundary + skill interop), and a "Model-driven onboarding" feature row.

## [1.4.0] - 2026-08-15

Identity migration: the fork now runs under its own name end to end — `openharness` (package/CLI/paths) → `iterate-harness` / `ih`.

### Changed

- **Python package renamed** `openharness` → `iterate_harness` (`src/openharness/` → `src/iterate_harness/`, all 1.8k+ imports and module-path strings across 271 files migrated). Class identifiers and user-visible strings followed (`OpenHarnessSessionBackend` → `IterateHarnessSessionBackend`, banners/messages now brand iterate-harness).
- **CLI entry points replaced**: `oh` / `openh` / `openharness` launchers removed; the package now installs `ih` (short) and `iterate-harness` (full) — `ih iterate review`, `ih iterate report --pr`, … `python -m iterate_harness` works too. All in-repo docs, install scripts (`install.sh` / `install.ps1` / `install_dev.sh`), the managed pre-commit hook (`shutil.which("ih")`), and bundled skill content now reference `ih`.
- **Data directories migrated** `~/.openharness/` → `~/.iterate-harness/` (sessions, settings, themes, plugins, worktrees, teams, copilot auth) and project-level `.openharness/` → `.iterate-harness/`. venv exclude paths and hatch wheel/force-include mappings updated to the new package layout. Existing local data under the old name is not migrated automatically — move it by hand if you have state worth keeping.
- Frontend workspace package renamed `@openharness/terminal` → `@iterate-harness/terminal` (package.json + lockfile).
- README (en + zh-CN) CLI cheat-sheet and feature tables now use `ih`; CONTRIBUTING / SHOWCASE rebranded; historical CHANGELOG / RELEASE_NOTES entries keep the original `oh` wording on purpose (they describe what shipped at the time).
- `pyproject` description keeps the "fork of OpenHarness" attribution; upstream links in README/CONTRIBUTING are preserved.

### Upgrade Notes

- After `git pull`, re-run the installer (or `uv pip install -e .`) so the new `ih` / `iterate-harness` console scripts are generated; stale `oh` shims from previous installs can be deleted.
- Managed pre-commit hooks installed by ≤1.3.0 embed the old absolute `oh` path — run `ih iterate hook uninstall && ih iterate hook install` to re-render them.
- Scheduled quick-review jobs store a `ih iterate review --changed …` command string resolved via PATH at fire time — jobs created by ≤1.3.0 still reference `oh` and must be re-registered (`ih iterate schedule add …`) after upgrading.

## [1.3.0] - 2026-08-15

CI visibility: PR comment mode, medium/low threshold gates, per-dimension USD estimates.

### Added

- PR comment mode (`openharness.iterate.pr_comment` + `oh iterate report --pr`): renders the final report as a Markdown PR comment (marker-anchored, findings table capped at 50 rows, threshold-gate status with inline violations) and posts it via the GitHub CLI. Idempotent — the hidden `<!-- iterate-report -->` marker lets subsequent runs UPDATE the existing comment instead of duplicating one per CI run (`gh api` PATCH by comment id; POST only when none exists yet). Every failure mode degrades to a `skipped` status without raising: gh not installed, no PR context, auth missing, API errors, 60s timeouts — so the `--fail-on` exit-code semantics stay untouched. `--pr` composes with `--github` (annotations + comment) and suppresses the plain-text render. The process boundary is a single injectable runner, keeping the module fully unit-testable.
- Threshold gates extended to `max_medium` / `max_low` (closes the v1.1 note): `thresholds` and `thresholds.dimensions.<dim>` now accept all four severity caps. Parsing (`_parse_threshold_metric`), gate evaluation (`evaluate_threshold_gates` counts findings at EXACTLY each severity, global + per-dimension), and yaml round-trip serialization are driven by one `SEVERITY_METRICS` tuple in `types.py`, so adding a metric later is a one-line change. Invalid values still degrade to per-field errors, never exceptions.
- Per-dimension USD estimates (closes the v1.2 note): `CostMeter.dimension_cost_usd(model)` converts the reviewer-reported per-dimension token totals into estimated USD at the model's blended (input+output)/2 price; `format_summary(dimension_model=…)` appends `(~$X.XXXX)` to each dimension line. Estimates stay OUT of `total_cost_usd` / `total_tokens` (main-loop metered accounting is never polluted by reviewer-reported figures).

### Changed

- `oh iterate report` renders plain text only when neither `--github` nor `--pr` is given.
- README (en + zh-CN): CI/PR 模式 row now mentions `--pr`; version badges updated to 1.3.0.

## [1.2.0] - 2026-08-15

Daily-driver ergonomics: detection-driven init wizard, managed pre-commit hook, engine-level per-dimension usage relay.

### Added

- Detection-driven config wizard (`openharness.iterate.init_wizard` + `oh iterate init` / `/iterate init`): probes project marker files (package.json / pyproject.toml / setup.py / requirements.txt / go.mod / Cargo.toml / Gemfile / pom.xml / build.gradle / composer.json), infers the language stack, derives the test command ONLY from explicit evidence (a real `scripts.test` entry, a pytest table or tests/ layout, a go.mod…), and recommends dimensions (frontend deps unlock `frontend-backend` / `ui-ux`). The wizard prints the evidence lines, previews the yaml, and writes `iterate.config.yaml` only after confirmation (`--yes` skips prompts; `--force` overwrites). Dimension selection accepts 1-based indexes or names (`2,4` / `security ui-ux`); the emitted yaml goes through `yaml.safe_dump` so goal text can never inject yaml structure. Malformed marker files degrade gracefully to evidence notes. Replaces the older marker-lite `init` command (same CLI name, richer behavior; `--defaults` became `--yes`).
- Managed pre-commit hook (`openharness.iterate.git_hook` + `oh iterate hook install|uninstall|status`): writes a MARKED `.git/hooks/pre-commit` that runs ONE dry-run changed-only review round (`oh iterate review --changed --clean-ok --ref HEAD --rounds 1`) and gates the commit via the exact CI exit-code policy (`--fail-on`, default `high`). Refuses to install over or remove foreign hooks (marker check); `ITERATE_SKIP_HOOK=1` / `git commit --no-verify` skips; the `oh` binary is resolved to an absolute path at install time because hook environments often lack the user's PATH. The generated script is pure POSIX sh.
- Engine-level per-dimension usage relay (closes the v1.1 audit gap): `iterate_review(operation="aggregate", dimension_usage=…)` now publishes the reported per-dimension token totals into the loop-policy state; `IterateLoopPolicy.on_turn_end` relays them into the `CostMeter` (monotonic max — running totals never double-count), so `format_summary()` and future progress reporting see reviewer-subagent spend without polluting main-loop token totals. Budget-stop and normal round paths both record; malformed usage maps are dropped defensively.

### Changed

- README (en + zh-CN): feature table gains the detection-driven init / pre-commit hook rows; the token-budget row now mentions the usage relay; the CLI block shows `oh iterate hook install`; version badges updated to 1.2.0.

## [1.1.0] - 2026-08-15

Policy layer: engine-enforced token budgets, project threshold gates, timezone-aware schedules.

### Added

- Whole-run token budget (`token_budget` in `iterate.config.yaml`): a positive integer enforced by the engine-level loop policy — once main-loop usage exceeds it the loop hard-stops with a `token budget exhausted (used/budget tokens)` reason and steers the model to the closing report. Enforcement is snapshot-independent (fires even on a turn without a fresh aggregate); `validate_config` rejects non-positive/non-integer values without killing the loop.
- Per-dimension budget auditing (`openharness.iterate.review.audit_dimension_budgets`): `iterate_review(operation="aggregate", dimension_usage={...})` now accepts reported per-dimension token usage and audits it against `dimension_resources.<dim>.token_budget`. The aggregate output gains a `budgetAudit` block (budget/used/remaining/exceeded per dimension); exhausted dimensions flow into the loop-policy state, where ALL budgets exhausted stops the loop and PARTIAL exhaustion injects an explicit "do NOT spawn reviewer agents for these dimensions" directive into the next-round instruction. Auditing never raises — malformed usage is clamped/skipped.
- Project threshold gates (`thresholds` in `iterate.config.yaml`): `max_critical` / `max_high` cap the number of findings at or above that severity, globally and per dimension (`thresholds.dimensions.<dim>`). Gates are evaluated in `iterate_review(operation="meta-review")` (pure `evaluate_threshold_gates`); a violated gate emits a `thresholdGate` block in the final report, folds one `THRESHOLD_EXCEEDED` meta-review issue per violation, and flips the verdict to `needs_revision`. The canonical-loop prompt directs the model to copy the block into the single report entry; CI (`oh iterate report`) renders `threshold gate: PASS/FAIL` (violations inlined, capped at 5) and fails the exit code via `threshold_exit_code` combined with `severity_gate`. Invalid threshold yaml entries are reported by `validate_config` and skipped.
- Timezone-aware scheduled reviews: `oh iterate schedule add <cron> --timezone <IANA>` (e.g. `Asia/Shanghai`) evaluates the 5-field cron expression in local time and stores the next run UTC-normalized; `next_run_time` / `upsert_cron_job` / `mark_job_run` all honor the job's `timezone` (unknown stored zones fall back to UTC), unknown zones are rejected with `ValueError` at install time, and the scheduler's due check compares against the stored UTC time so a 09:00 Beijing job fires at 01:00 UTC.

### Changed

- README (en + zh-CN): feature table gains the token budgets / threshold gates / schedule timezones rows; version badges updated to 1.1.0.

## [1.0.0] - 2026-08-15

First stable release: shareable single-file HTML report, chronological decision replay, per-dimension resource overrides.

### Added

- Single-file HTML report (`openharness.iterate.html_report` + `oh iterate report --html [path]` / `/iterate report --html`): renders the final report entry plus the fix timeline (atomic_fix / revert / validation entries before the report, capped at 50) into ONE self-contained `.html` file — inline CSS only, zero external requests, no scripts. Includes an inline SVG convergence curve (findings per round with per-point labels), severity + dimension distribution bars, the full findings table (failure scenario + suggested fix per finding), colorized unified diffs from `atomic_fix` entries, and verdict/mode/converged badges. All log-derived text is HTML-escaped; severity colors come from a fixed table so log content cannot inject markup. Default output path: `.iterate/report.html`; the `--fail-on` severity gate still decides the exit code so CI can upload the artifact AND gate on it.
- Decision-log replay (`openharness.iterate.replay` + `oh iterate log --replay` / `/iterate log --replay`): re-plays the whole run chronologically with relative timestamps (`[+90s] r1 review_result newFindings=3`), a per-type one-line summary (goal / findings / fix / command / verdict probes), unknown-type payload previews, and truncation at 140 chars. Unparseable timestamps degrade to `[+?s]`; an empty log prints a friendly placeholder.
- Per-dimension resource overrides (`dimension_resources` in `iterate.config.yaml`): each dimension can set `model` (e.g. a strong model for security, a fast one for style-tests), `concurrency` (clamped to 1–8) and `token_budget` (non-negative). Values are parsed defensively (invalid entries are reported by `validate_config` and skipped, never fatal), flow into the review plan (`DimensionPlan.resources`, serialized in `plan_to_dict`) and are appended to the reviewer prompt as an explicit spawn directive ("Resource plan: model=…; max concurrent reviewer agents=…; token budget=…"). `/iterate config` lists the effective per-dimension resources.

### Changed

- README (en + zh-CN): feature table gains the HTML report / decision replay / per-dimension resources rows; the CLI block shows `oh iterate report --html` and `oh iterate log --replay`; version badges updated to 1.0.0.

## [0.6.0] - 2026-08-15

Unattended scenarios: changed-only quick review, multi-repo batch ranking, scheduled reviews.

### Added

- Changed-only quick review (`openharness.iterate.git_scope`): `oh iterate review --changed [--ref <ref>]` / `oh iterate run --changed` / `/iterate review --changed` collect the delta via `git diff --name-only <ref>` + `git status --porcelain` (renames contribute the new path, only on-disk files qualify, ref tokens are validated against an option-injection pattern, 200-file cap) and pin the entire loop to those files. The kickoff embeds the file list and directs the model to `iterate_review(operation="plan", changed_files=[...])`; the plan then flips to `changed-only` scope and every per-dimension reviewer prompt carries the explicit listing. `--clean-ok` exits 0 on a clean tree for scheduled runs.
- Multi-repo batch review with ranking (`openharness.iterate.batch` + `oh iterate batch <repo...>`): reviews each repo sequentially through the headless print pipeline (per-repo stdout captured, one failing repo never kills the batch), then ranks all repos worst-first by a severity-weighted score (critical 10 / high 5 / medium 2 / low 1). Clean repos and errors are surfaced as their own statuses; `--json` emits machine-readable records, `--full` opts out of changed-only.
- Scheduled quick review (`oh iterate schedule add <cron> [--ref] [--rounds] [--mode] [--timeout]`): registers the `iterate.review-changed` cron job (UTC, 5-field) that runs `oh iterate review --changed --clean-ok` in the repo; `schedule remove` / `schedule status` manage it, and cross-run new-vs-stubborn findings surface via the trend library (`oh iterate log --trend`). Cron jobs gained an optional per-job `timeout` field (default 300s, clamped to [1, 7200]s) honored by `execute_job`.

### Changed

- README (en + zh-CN): feature table gains changed-only quick review / batch ranking / scheduled review rows; the CLI block shows `--changed`, `oh iterate batch` and `oh iterate schedule add`.

## [0.5.0] - 2026-08-15

Data accumulation + UX polish: finding trend library, componentized Esc menu, breakpoint resume.

### Added

- Finding fingerprint trend library (`openharness.iterate.trend_store`): every finished run fingerprints its findings (`file|line|dimension` SHA-1, line optional for file-level findings) into `.iterate/trend-library.json` (atomic write, corrupt data resets to empty, 2000-record LRU prune). Cross-run classification: **new** (first sighting), **fixed** (open before, absent now), **regressed** (fixed before, back now), **stubborn** (open for 3+ runs). Surfaced via `oh iterate log --trend`, `/iterate trend` and `/iterate log trend`; recorded automatically when the report entry lands in the decision log.
- Breakpoint resume: `openharness.iterate.last_state` summarizes the last finished run from the decision log (mode, verdict, rounds, severity buckets, top-3 finding preview, last Esc intervention). The React TUI backend emits a `last_loop_state` event at startup and the new `IterateResumePanel` renders it above the status bar (auto-hidden once a live `review_progress` dashboard appears). `/iterate resume` re-kicks the loop with a resume prompt that embeds the previous verdict/findings and instructs re-verification of still-reproducing findings; `iterate_state` now persists in session snapshots so mid-loop state survives restarts.
- Directional-key Esc intervention menu: new `AskUserSelect` channel end-to-end (`QueryContext.ask_user_select` → `ui.runtime` → backend host `_ask_select`). The TUI renders `select_prompt` modals with the existing SelectModal component (↑↓ navigate, Enter select, number-key quick select, Esc submits the safe first option); the pause menu is now a 4-option componentized menu instead of free-text `s`/`n`/`x` input. Non-TUI frontends keep the free-text question fallback; every decision is still logged as an intervention entry.

### Changed

- README (en + zh-CN): feature table gains the trend library / breakpoint resume rows, the Esc intervention row now describes the directional-key menu, and the CLI block shows `oh iterate log --trend`.

## [0.4.0] - 2026-08-15

Esc mid-loop intervention + dependency vulnerability cleanup.

### Added

- Esc intervention (design §11.2.1): pressing Esc while an iterate loop is running no longer hard-cancels the turn — the loop pauses at the next round boundary and surfaces the intervention menu through the existing question modal: `s` skip the current top finding, `n <dimensions>` narrow the review scope, `x` stop now, empty/anything else resumes the normal loop. A second Esc force-interrupts the current turn. Every intervention decision is appended to the decision log (`type=decision`, `kind=intervention`); headless sessions (no interactive channel) default to a safe stop. `QueryEngine.submit_message` drops stale pause flags so a leftover request cannot fire on a later run.

### Changed

- Dependency security cleanup (all 10 dependabot alerts, 5 high): `autopilot-dashboard` vite `^6.3.2` → `^6.4.3` (pulls patched postcss/esbuild/@babel/core); `frontend/terminal` marked `^18.0.0` → `^18.0.9`, tsx `^4.19.2` → `^4.23.12` (esbuild 0.28.2, ws 8.21.3). Both packages now report 0 npm-audit vulnerabilities; vite build and `tsc --noEmit` verified.

## [0.3.0] - 2026-08-15

Interactive safety for autonomous fixes + CI consumption of the final report.

### Added

- Per-fix diff approval (`Settings.iterate.require_fix_approval`): while a normal-mode loop is active, mutating file tools (`write_file` / `edit_file` / `notebook_edit`, incl. `file_write`/`file_edit` aliases) are routed through the interactive permission prompt with an inline diff preview (clipped to 40 lines / 200 chars per line) — even in full-auto mode. Hard denials (protected paths, forbidden fix patterns) are never downgraded into a confirmation, and dry-run reviews never trigger the gate.
- `openharness.iterate.ci_report`: renders the final `report` entry from the decision log as GitHub Actions workflow commands (per-finding annotations with `file=`/`line=` properties and full workflow-command escaping), plain text, and a severity exit-code gate (`none|low|medium|high|critical`, default `high`). Missing or malformed reports degrade to an empty report instead of failing the pipeline.
- `oh iterate report` CLI subcommand (`--github`, `--fail-on`) and `/iterate report` slash subcommand for REPL parity.

## [0.2.0] - 2026-08-15

First iterate-harness release: a focused fork of OpenHarness v0.1.9 dedicated
to the iterate review/fix loop (semantic layer ported from the iterate skill).

### Added

- `openharness.iterate` semantic layer (Python port of the TS skill): deterministic review engine (plan / aggregate / meta-review), Master+Overrides config loader, EXACT-match validation runner, append-only decision log, per-project structured personalization, cost meter (tokens → USD), git worktree isolation flow, and canonical dry-run/normal loop prompts.
- Six `iterate_*` tools registered in the kernel tool registry: `iterate_config`, `iterate_validate`, `iterate_review`, `iterate_decision_log`, `iterate_context`, `iterate_triage`.
- Engine-level `IterateLoopPolicy` in the kernel query loop: deterministic convergence enforcement, round caps, next-round steering, `ReviewProgressEvent` emission, and cost accumulation — auto-attached when `settings.iterate.enabled` is on.
- `review_progress` protocol event wired through the React TUI backend host: live convergence dashboard panel (per-round findings trend, per-dimension counts, running USD cost, converged badge); print mode emits the same progress to stderr / stream-json.
- `iterate_triage` interactive findings triage: y (fix) / n (skip) / a (always-ignore); `a` answers persist to `known_intentional` personalization and are filtered from future review rounds; headless sessions apply a configured default decision.
- `protected_paths` and `forbidden_fix_patterns` from `Settings.iterate` are auto-assembled into the permission layer via `build_permission_checker` (deny path rules normalized to absolute-path globs + write-payload regex boundary evaluated before tool allowlists).
- `/iterate` slash command (status / review / run / log / config / validate) and `oh iterate init|review|run|resume|log` CLI subcommands; bundled `iterate` skill.
- `ITERATE.md` project knowledge discovery alongside CLAUDE.md; iterate state survives microcompaction via a dedicated compact attachment.

### Changed

- Fork identity: package renamed to `iterate-harness` with an `iterate-harness` console script; README/README.zh-CN rewritten for the fork; install.sh / install.ps1 / install_dev.sh install from `jingzhao-l/iterate-harness` (git clone + editable), register `oh` + `iterate-harness`, and no longer reference the removed `ohmo` / channels stack.

## [0.1.9] - 2026-05-07

### Added

- Added a bundled `skill-creator` skill for creating, improving, and verifying OpenHarness/ohmo skills.
- User-invocable skills can now be triggered directly as slash commands, with support for skill-specific arguments and model override metadata.

### Fixed

- `oh setup` can now update the API key for an already-configured API-key provider profile instead of only changing the model.
- `oh provider edit <profile> --api-key <key>` can now replace a saved profile API key, and `oh provider add ... --api-key <key>` can store one during profile creation.

## [0.1.8] - 2026-05-06

### Added

- Built-in `nvidia` provider profile so `oh setup` offers NVIDIA NIM as a first-class OpenAI-compatible provider choice, with `NVIDIA_API_KEY` auth source, `openai/gpt-oss-120b` as the default model, and the NVIDIA NIM endpoint.
- Built-in `qwen` provider profile so `oh setup` offers Qwen (DashScope) as a first-class provider choice, with `dashscope_api_key` auth source, `qwen-plus` as the default model, and the DashScope OpenAI-compatible endpoint.
- Plugin tool discovery: plugins can now provide `BaseTool` subclasses in a `<plugin>/tools/` directory and they are auto-discovered, instantiated, and registered in the tool registry at runtime. Add `tools_dir` to `plugin.json` (defaults to `"tools"`).
- `oh --dry-run` safe preview mode for inspecting resolved runtime settings, auth state, prompt assembly, commands, skills, tools, and configured MCP servers without executing the model or tools.
- Built-in `minimax` provider profile so `oh setup` offers MiniMax as a first-class provider choice, with `MINIMAX_API_KEY` auth source, `MiniMax-M2.7` as the default model, and `MiniMax-M2.7-highspeed` in the model picker.
- Docker as an alternative sandbox backend (`sandbox.backend = "docker"`) for stronger execution isolation with configurable resource limits, network isolation, and automatic image management.
- Built-in `gemini` provider profile so `oh setup` offers Google Gemini as a first-class provider choice, with `gemini_api_key` auth source and `gemini-2.5-flash` as the default model.
- `diagnose` skill: trace agent run failures and regressions using structured evidence from run artifacts.
- OpenAI-compatible API client (`--api-format openai`) supporting any provider that implements the OpenAI `/v1/chat/completions` format, including Alibaba DashScope, DeepSeek, GitHub Models, Groq, Together AI, Ollama, and more.
- `OPENHARNESS_API_FORMAT` environment variable for selecting the API format.
- `OPENAI_API_KEY` fallback when using OpenAI-format providers.
- GitHub Actions CI workflow for Python linting, tests, and frontend TypeScript checks.
- `CONTRIBUTING.md` with local setup, validation commands, and PR expectations.
- `docs/SHOWCASE.md` with concrete OpenHarness usage patterns and demo commands.
- GitHub issue templates and a pull request template.
- React TUI assistant messages now render structured Markdown blocks, including headings, lists, code fences, blockquotes, links, and tables.
- Built-in `codex` output style for compact, low-noise transcript rendering in React TUI.

### Fixed

- Subprocess teammate spawn (`agent` tool, `task_create`) now works on Windows under Git Bash. `subprocess_backend.spawn` builds a direct-exec `argv` list and passes it through new `argv=` and `env=` kwargs on `BackgroundTaskManager.create_agent_task` / `create_shell_task`; `_start_process` then runs the executable via `asyncio.create_subprocess_exec(*argv)` with no shell in between. Previously the spawn command was a single string interpreted by `bash -lc`, which on Windows could not reliably exec a Windows-pathed Python interpreter (e.g. `C:\Users\...\python.exe`) — Git Bash's escape parser consumed the backslashes from the embedded env-prefix and, even with proper quoting, bash launched via `asyncio.create_subprocess_exec` returned `command not found` for Windows-pathed binaries that worked perfectly when invoked interactively. Bypassing the shell sidesteps the entire class of cross-platform quoting and path-translation hazard. The legacy shell-evaluated `command=` path is preserved for callers (e.g. `BashTool`) that legitimately want shell semantics. See issue #230.
- Bundled skill loader now uses `yaml.safe_load` for SKILL.md frontmatter, matching the user-skill loader. The shared parser is extracted to `openharness.skills._frontmatter` so bundled and user skills handle YAML block scalars (`>`, `|`), quoted values, and other standard YAML constructs the same way.
- Compaction now detects llama.cpp/OpenAI-compatible context overflow errors, accounts for image blocks in auto-compact token estimates, and strips image payloads from summarizer-only compaction requests.
- Large tool results are now bounded in conversation history: oversized outputs are saved under `tool_artifacts`, old MCP results become microcompactable, and context collapse trims stale tool-result payloads.
- ohmo now keeps personal memory isolated from OpenHarness project memory: `/memory` in ohmo sessions targets the ohmo workspace memory store, and ohmo runtime prompt refreshes no longer inject project memory unless explicitly requested.
- Fixed `glob` and `grep` tools hanging indefinitely when the `rg` subprocess produced enough stderr output to fill the OS pipe buffer. `stderr` is now redirected to `DEVNULL` so it is discarded rather than blocking the child process.
- Fixed `bash_tool` hanging after a timed-out command when the subprocess stdout stream stayed open. `_read_remaining_output` now applies a 2-second `asyncio.wait_for` timeout so the tool always returns promptly.
- Fixed `session_runner` background task deadlock caused by an unread `stderr=PIPE` stream. The subprocess now uses `stderr=STDOUT` so all output merges into the single readable stdout pipe.
- React TUI prompt input now treats the raw DEL byte (`0x7f`) as backward delete while preserving true forward-delete escape sequences, fixing backspace failures seen in some macOS terminal environments.
- `todo_write` tool now updates an existing unchecked item in-place when `checked=True` instead of appending a duplicate `[x]` line.

- Built-in `Explore` and `claude-code-guide` agents no longer hard-code `model="haiku"`, which caused them to fail for users on non-Anthropic providers (OpenAI, Bedrock, custom base URLs, etc.). Both agents now use `model="inherit"` so they run with whatever model the parent session is using. `build_inherited_cli_flags` is also fixed to skip the `--model` flag entirely when the value is `"inherit"`, letting the subprocess correctly inherit the parent model via the `OPENHARNESS_MODEL` environment variable instead of receiving the literal string `"inherit"` as a model name.

- React TUI spinner now stays visible throughout the entire agent turn: `assistant_complete` no longer resets `busy` state prematurely, and `tool_started` explicitly sets `busy=true` so the status bar remains active even when tool calls follow an assistant message. `line_complete` is the sole signal that ends the turn and clears the spinner.
- Skill loader now uses `yaml.safe_load` to parse SKILL.md frontmatter, correctly handling YAML block scalars (`>`, `|`), quoted values, and other standard YAML constructs instead of naive line-by-line splitting.
- `BackendHostConfig` was missing the `cwd` field, causing `AttributeError: 'BackendHostConfig' object has no attribute 'cwd'` on startup when `oh` was run after the runtime refactor that added `cwd` support to `build_runtime`.
- Shell-escape `$ARGUMENTS` substitution in command hooks to prevent shell injection from payload values containing metacharacters like `$(...)` or backticks.
- Swarm `_READ_ONLY_TOOLS` now uses actual registered tool names (snake_case) instead of PascalCase, fixing read-only auto-approval in `handle_permission_request`.
- Memory scanner now parses YAML frontmatter (`name`, `description`, `type`) instead of returning raw `---` as description.
- Memory search matches against body content in addition to metadata, with metadata weighted higher for relevance.
- Memory search tokenizer handles Han characters for multilingual queries.
- Fixed duplicate response in React TUI caused by double Enter key submission in the input handler.
- Fixed concurrent permission modals overwriting each other in TUI default mode when the LLM returns multiple tool calls in one response; `_ask_permission` now serialises callers via an `asyncio.Lock` so each modal is shown and resolved before the next one is emitted.
- Fixed React TUI Markdown tables to size columns from rendered cell text so inline formatting like code spans and bold text no longer breaks alignment.
- Fixed grep tool crashing with `ValueError` / `LimitOverrunError` when ripgrep outputs a line longer than 64 KB (e.g. minified assets or lock files). The asyncio subprocess stream limit is now 8 MB and oversized lines are skipped rather than terminating the session.
- Fixed React TUI exit leaving the shell prompt concatenated with the last TUI line. The terminal cleanup handler now writes a trailing newline (`\n`) alongside the cursor-show escape sequence so the shell prompt always starts on a fresh line.
- Reduced React TUI redraw pressure when `output_style=codex` by avoiding token-level assistant buffer flushes during streaming.

### Changed

- ohmo Feishu group routing now supports managed group creation, gateway-scoped provider/model commands, and stricter group mention handling so group conversations only wake ohmo when explicitly addressed.
- Dry-run output now reports a `ready` / `warning` / `blocked` readiness verdict, concrete `next_actions`, likely matching skills/tools for normal prompts, and richer slash-command previews for read-only vs stateful command paths.
- React TUI now groups consecutive `tool` + `tool_result` transcript rows into a single compound row: success shows the result line count inline (e.g. `→ 24L`), errors show a red icon and up to 5 lines of error detail beneath the tool row. Standalone successful tool results are suppressed to reduce transcript noise; standalone errors are still surfaced.
- README now links to contribution docs, changelog, showcase material, and provider compatibility guidance.
- README quick start now includes a one-command demo and clearer provider compatibility notes.
- README provider compatibility section updated to include OpenAI-format providers.

## [0.1.7] - 2026-04-18

### Fixed

- Install script now links `oh`, `ohmo`, and `openharness` into `~/.local/bin` instead of prepending the virtualenv `bin` directory to `PATH`, which avoids overriding Conda-managed shells while preserving global command discovery.
- React TUI prompt now supports `Shift+Enter` for inserting a newline without submitting the current prompt.
- React TUI busy-state animation is less error-prone on Windows terminals: the extra pseudo-animation line was removed, Windows now uses conservative ASCII spinner frames, and the spinner interval was slightly slowed to reduce flashing.

## [0.1.0] - 2026-04-01

### Added

- Initial public release of OpenHarness.
- Core agent loop, tool registry, permission system, hooks, skills, plugins, MCP support, and terminal UI.

The format is based on Keep a Changelog, and this project currently tracks changes in a lightweight, repository-oriented way.
