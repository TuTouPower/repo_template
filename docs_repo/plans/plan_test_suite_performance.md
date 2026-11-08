# 测试套件性能优化方案

> 编写时间：2026-11-08T10:13:03+08:00。分析基线：`697c0b725a95a5077d5b2e40ed989b83fc38cac0`。
>
> 文档性质：下一轮优化实施方案。本文定义目标架构、实施顺序和验收条件；方案中的改造尚未实施，性能目标尚未验证。历史治理状态以[裁决总账](../decision_log.md)为准，编写本文不表示改造已经获批或完成。
>
> 适用范围：模板工厂的 `factory_tests/`、工厂测试配置，以及确有重复 Git I/O 的 `repo/.repo_template/scripts/repo_task/`。面向维护测试基础设施和工具链的开发者。

## 1. 方案结论与范围

优先治理测试 Arrange（安排初始状态）阶段反复启动 CLI 和命令内部重复查询 Git 的成本。pytest 的 call 同时包含 Arrange、被测操作和断言，必须进一步拆分，不能把全部 call 耗时归为启动解释器，也不能只优化 fixture 就推断全量收益。

按以下顺序优化：

1. 降低重复安排成本：明确测试目标，使用领域 API 构造非被测前置状态；保留 CLI、跨进程锁和完整生命周期回归。
1. 降低命令内部重复 Git I/O：按确定 SHA 复用只读结果，使用单次非交互 batch 读取，不建立交互式或常驻子进程。
1. 降低 fixture 成本：每个 worker 构建种子，每个用例复制完整独立仓库，默认普通复制。每个用例独占 `.git`、runtime、锁与 worktree 登记；worktree 在用例私有仓库内真实创建。

种子复制是初始化优化，不能单独承担全量提速目标。APFS CoW、并行 worker 数量、Git 可执行入口和文件系统配置在受控测量后选优。RAM Disk 不作为默认方案。

### 1.1 目标与非目标

|类别|内容|
|---|---|
|目标|保持用例行为、断言、错误处理和隔离边界，降低全量 wall time、初始化次数与 Git 子进程数|
|目标|明确所有可变状态所有权，消除测试对宿主 Git 配置和共享仓库状态的隐式依赖|
|目标|建立可重复的基准，分别报告 setup、call、teardown 和总耗时|
|非目标|改变 task 生命周期、review、锁、迁移或同步公开语义|
|非目标|通过跳过测试、减少关键断言、全局禁用 hooks 或弱化文件变更检测提速|
|非目标|引入常驻测试服务、跨运行持久种子缓存或通用仓库恢复框架|
|非目标|把工厂测试辅助代码分发到消费仓，或扩大消费仓默认测试范围|

本方案延续既有 xdist 并行化方向。上一轮并行改造及其历史数据见[宏观改造方案 H3 与实施记录](../archive/plan_macro_overhaul_2026_11.md)；本文不替换消费仓操作手册。

## 2. 已验证现状与证据限制

本节为上述 commit 的测量快照，不表示以后版本仍有相同用例数和耗时。

### 2.1 环境与全量基线

|项目|实测值|
|---|---|
|系统|macOS，Darwin 25.5.0，arm64|
|CPU / 内存|10 个物理核、10 个逻辑核；64 GiB 内存|
|Python / pytest / xdist|3.14.7 / 9.2.0 / 3.8.0；pytest 使用独立 uv tool 环境|
|Git|2.50.1，Apple Git-155；PATH 命中 `/usr/bin/git`|
|测试收集|1,055 个用例，0.31 秒；其中 299 个带 contract marker|
|全量结果|1,054 passed、1 skipped；10 worker；240.05 秒|
|最慢用例|`test_run_begin_supports_task_added_and_scheduled_on_main_after_unmerged_chain_fork`，call 51.16 秒|
|慢项分布|`--durations=25` 前 25 项全部为 call，没有 fixture setup|

全量实际执行命令，在工厂根运行：

```bash
pytest repo/.repo_template/tests factory_tests -q -n auto \
  -p no:cacheprovider --basetemp "$(mktemp -d)" --durations=25
```

测量期间存在明显主机干扰：全量之后的抽样中，load average 约 112–127，CPU idle 约 0.63%；内存约 36 GiB 处于压缩状态。不能据此认定全量运行期间始终处于相同负载，也不能把 240.05 秒当成空闲主机标准基线。

### 2.2 重复 fixture 分布

通过收集结果解析实际 fixture 定义，包含跨测试模块导入复用：

|定义入口|使用用例数|重复构建内容|
|---|---|---|
|[test_dispatch_integration.py](../../factory_tests/test_dispatch_integration.py) 的 git_repo|237|脚本与模板、三个 backlog task、Git 初始化与首次提交|
|[test_task_start_flow.py](../../factory_tests/test_task_start_flow.py) 的 git_repo|107|与 dispatch 高度重复的三任务仓库|
|[test_goal.py](../../factory_tests/test_goal.py) 的 git_repo|19|两任务工作流仓库|
|[test_repo_sync.py](../../factory_tests/test_repo_sync.py) 的 env|133|最小模板源仓、消费仓与同步状态|

三套主要工作流 fixture 合计覆盖 363 个用例，不代表仓内全部真实 Git fixture。同步 env 另覆盖 133 个用例。

### 2.3 代表用例剖析

串行运行以下四个用例，通过临时内存 hook 统计 pytest 阶段及当前测试进程的 `subprocess.run`，并用 `GIT_TRACE2_EVENT` 观察 Git 子进程：

|模块与用例|setup / 秒|call / 秒|
|---|---|---|
|test_run_commands：test_run_begin_supports_task_added_and_scheduled_on_main_after_unmerged_chain_fork|1.919|60.183|
|test_dispatch_integration：test_verify_ready_with_valid_handoff|0.334|5.378|
|test_task_start_flow：test_start_keeps_main_unchanged_and_activates_only_worktree|0.376|1.227|
|test_repo_sync：test_sync_state_atomic_update_preserves_unknown_keys|0.254|0.002|

结果：4 passed，69.93 秒。setup 合计约 2.88 秒，call 合计约 66.79 秒。

|统计口径|次数|耗时 / 秒|
|---|---|---|
|测试进程在 setup 中直接调用 Git|22|2.617|
|测试进程在 call 中直接调用 Git|129|3.555|
|测试进程在 call 中直接调用其他子进程，主要为 Python 命令|30|63.128|
|Trace2 记录 Git 执行，包含 CLI 内部调用与 Git 子命令|2,070|exit 的 t_abs 累加约 9.99|

Trace2 中最多的命令为 rev-parse 560 次、show 513 次、ls-tree 192 次、worktree 169 次。该数据仅覆盖四个用例，不能外推为全量总数。

外层子进程等待包含内部 Git 执行，不能把两者相加。Trace2 的 t_abs 也不是完整父进程等待时间；两者差值包含启动入口、调度等待、Python 执行和其他 I/O，不能全部归因于 Python import。计时 hook 和 Trace2 本身也有观测成本。

### 2.4 复制与初始化微基准

对 dispatch fixture 及其初始仓库各重复 12 次，得到以下中位数。种子约 131 个文件，`repo_task` 包含 29 个 Python 源文件，合计约 452 KB。

|操作|中位数 / ms|
|---|---|
|现有 dispatch fixture 完整构建|461.72|
|shutil.copytree，复制完整仓库|109.05|
|cp -cR，复制完整仓库|154.26|
|git clone --local --no-hardlinks|448.57|

微基准受主机负载影响，部分时段与代表用例剖析重叠；不同方法按批次测量，没有随机交错，不能视为受控 A/B。复制耗时不包含首次 Git 查询与清理，以上数字不构成稳定加速倍数。

对种子执行一次 repack / prune-packed 后，文件数降至约 76，但首次 status 耗时上升且波动明显，未证明总体收益。`rev-parse HEAD` 的入口对比中，`/usr/bin/git` 约 88.97 ms、Xcode 实际 Git 约 40.16 ms，同样仅为待复测信号。

本轮详细原始 trace 与微基准目录为临时产物，未归档；表内数据是实验摘要。下一轮受控测量必须保留环境、原始记录和计算口径，才能作为性能验收依据。

### 2.5 收益预算与优先级

仅作量级估算：`(461.72 - 109.05) ms × 363 ≈ 128 秒`，是受影响用例累计经过时间的估算，不是 CPU 时间。若工作均匀分配给 10 worker，折算 wall time 约 12.8 秒，约为本轮 240.05 秒的 5%。该计算未计各 profile 差异、种子构建、首次 Git 查询、清理和调度尾部，且微基准不受控，因此既不是确定收益，也不是严格上下界。

现有证据不支持把种子复制解释为全量提速 30%，也不支持固定的 Git 调用降幅承诺。各工作包需要分别报告节省的调用次数、累计耗时和实际 wall time，避免将总工作量降低等同于关键路径缩短。

四个代表用例以 call 为主要耗时，但不能据此声称全套 90% 均在 call。当前最慢用例包含重复前置安排，也包含正在验证的 add/edit、run-begin、finish、run-close 和整链合并；后者必须保持真实执行。P0 应区分安排辅助函数、被测 CLI 及其内部 Git 成本，随后优先处理可复用的安排热点。

## 3. 隔离边界与复用裁决建议

### 3.1 方案比较

|方案|主要问题|建议|
|---|---|---|
|session fixture 返回同一仓库|工作区、Git、runtime 与业务状态共用|不采用|
|每 worker 一个仓库，用例间 reset / clean|无法完整清理 config、hooks、refs、reflog、worktree 登记和遗留进程|不采用|
|共享 main / bare 仓库，每用例独立 worktree|共用 refs、对象库、默认 config 与本仓控制面|不采用|
|完整仓库普通复制|独立目录、文件与 Git 状态；存在小文件复制成本|首版默认|
|完整仓库 CoW 复制|数据块共享，写入独立；仍需遍历与创建元数据|实测通过后采用|
|整树硬链接|写文件、改权限、追加 reflog 或操作锁文件可能影响其他副本|不采用|
|git clone --shared / alternates|依赖源对象存活及 GC 策略|严格隔离场景不采用|
|git clone --local --no-hardlinks|对象独立，但 remote、refs、config 和未跟踪内容与原 fixture 不等价|仅用于测试 clone 本身的场景|

Git 对 worktree 共享 refs/config、local clone 硬链接和 alternates 生命周期的定义，见附录来源。普通 Git 操作通常把对象视为不可变数据，但对象破坏测试可以直接写对象文件；严格隔离方案因此不共享对象 inode。

### 3.2 本仓共享 worktree 的具体风险

[RepoContext](../../repo/.repo_template/scripts/repo_task/context.py) 通过 `--git-common-dir` 解析主工作区。runtime ledger、audit 与 worktree 名称依赖该身份；[locks.py](../../repo/.repo_template/scripts/repo_task/locks.py) 把控制锁和 task ID 锁放在 Git common directory。

共享 common directory 后，即使分支名唯一，仍可能出现：

- 相同 tid 和 runtime 路径互相污染，worktree 登记与 effective state 发现其他测试状态。
- refs / index 等 Git 锁冲突，通常表现为命令拒绝，而非必然死锁。
- 业务控制锁把并行用例串行化。
- 父进程持锁等待子进程，子进程申请同一把锁，形成等待环；当前代码已有同步验证期间的同仓控制面保护。

为每个测试增加唯一分支名不能解决上述问题。完整复制 `.git` 后，同名 `t001_alpha` 和 `repo_t001` 可以在不同用例目录中安全共存。

### 3.3 “100% 隔离”的可验收定义

目标是用例之间不存在未经声明的可变资源共享，包括文件内容、Git 状态、锁 inode、runtime、临时路径、配置与后台进程。种子是只读输入，其数据块可由文件系统通过 CoW 共享。

xdist 隔离 worker 进程，但不会为每个用例重新启动解释器。模块全局变量、环境修改与线程仍需 fixture 恢复；端口使用系统分配的空闲端口，服务与子进程需要显式停止和回收。若测试必须验证全新解释器、进程启动或跨进程锁语义，保留真实子进程。

同一用户权限下的测试不是恶意代码沙箱。任何能任意访问宿主路径的测试都可以越过约定；本方案不声称提供操作系统权限隔离。若另有不可信代码隔离要求，应单独设计容器或虚拟机执行环境。

## 4. 目标 fixture 架构

### 4.1 生命周期与目录所有权

```text
本次 pytest basetemp/
└── worker gw0/
    ├── seeds/
    │   ├── workflow_three_tasks/
    │   ├── workflow_two_tasks/
    │   └── sync_source/
    ├── test_a/
    │   ├── repo/.git/
    │   ├── repo_t001/
    │   └── bin_md/
    └── test_b/
        ├── repo/.git/
        └── repo_t001/
```

session fixture 使用 `tmp_path_factory`，函数级 fixture 使用 `tmp_path`。二者均位于本次运行的系统临时目录；worker 名不是唯一性来源，完整运行目录同时区分不同 pytest 会话。session fixture 在 xdist 中按 worker 创建，因此无需共享种子初始化锁。

种子只构建实际使用的固定 profile；避免全部 profile 在每个 worker 启动时预建。每个 profile 表达现有测试所需初始语义，不引入任意配置组合。dispatch 与 start_flow 经内容、配置和历史等价核对后才合并为三任务 profile；goal 保留两任务 profile。

分析基线中，start_flow 的 `_valid_task_body()` 只是对同一 `progress.md` 调用 `parse_front_matter()` 并返回 body，与 dispatch 的内联读取等价。本次实际比较模板路径、body 和默认 spec 均相同；不能因 helper 名称不同认定数据不同。该局部等价不替代完整 profile 核对，实施时仍需比较文件、模式位、配置、refs、提交树及目录外替身。

### 4.2 种子发布不变量

- `.git` 为种子内部真实目录，不是 gitfile、软链或外部 Git directory。
- 无 linked worktree 登记、alternates、外部绝对 `core.worktree`、运行态 ledger 和未结束事务。
- 初始文件、模式位、软链与配置经过核对；`HEAD` 和目标分支正确，初始提交语义与原 fixture 一致。
- 种子构建失败即使 fixture 失败，不发布半成品，不回退到旧缓存。
- 构建完成后不再对种子执行会刷新 index、写日志或触发维护的命令；所有验证在发布前完成，之后只读复制。
- 通过生命周期和辅助接口限制写入，不给种子递归 chmod 为只读，否则副本可能继承不可写权限。完整性审计使用文件读取和内容摘要。
- 种子不得记录用例目录、fake binary 或原始构建路径的绝对引用。

种子不包含预建 worktree。复制已关联 worktree 的目录会涉及 `.git` gitfile、`gitdir`、`commondir` 及运行态路径重定位，维护成本和错误面不符合当前需求。

### 4.3 代码组织与示意

|落点|改造内容|
|---|---|
|`factory_tests/git_repo_support.py`，拟新增|固定 profile 的种子构建、独立复制与必要验证；不依赖测试模块|
|`factory_tests/conftest.py`|session 种子 fixture、函数级 fixture 或仓库工厂注册|
|三个工作流测试模块及其导入方|移除重复构建，按现有初始语义选 profile；清理测试模块之间的 fixture 导入|
|`factory_tests/task_flow_support.py`|继续保留 finish 等领域测试辅助，不与仓库复制职责混合|
|`test_repo_sync.py` 的 env|复用固定源仓；消费仓与路径绑定继续按用例构造|

以下是目标结构示意，构建函数和替身安装函数需在实施时从现有 fixture 提取，不是当前可直接运行的代码：

```python
@pytest.fixture(scope="session")
def workflow_three_tasks_seed(tmp_path_factory):
    seed = tmp_path_factory.mktemp("workflow_three_tasks_seed") / "repo"
    build_workflow_three_tasks_seed(seed)
    validate_seed(seed)
    return seed


@pytest.fixture
def git_repo(tmp_path, workflow_three_tasks_seed, monkeypatch):
    repo = tmp_path / "repo"
    shutil.copytree(workflow_three_tasks_seed, repo, symlinks=True)
    install_fake_md_kx(tmp_path, monkeypatch)
    return repo
```

同步 env 的 `template_source.value`、state 路径及模块路径 monkeypatch 必须绑定到本用例副本，不能指向种子。consumer 当前是未提交的初始仓库，需保持该语义，不能套用含初始 commit 的工作流种子。

### 4.4 配置与资源隔离

在 worker 的种子构建前建立受控 Git 环境，并让用例子进程继承：

- 屏蔽宿主 system/global Git 配置，使用明确的测试配置文件；不修改用户全局配置。
- 清理继承的 `GIT_DIR`、`GIT_WORK_TREE`、`GIT_INDEX_FILE`、`GIT_COMMON_DIR`、对象目录、alternates、config 注入变量等定位或覆盖来源。
- 从系统临时目录执行非仓库测试，设置明确的 Git 搜索上界，避免误识别工厂仓。
- 用户名、邮箱、签名、换行与初始分支策略显式定义；测试配置加载本身的用例自行声明其输入。
- 使用显式空 template 目录初始化基础种子，避免宿主 template 注入 hooks。真实 hook 测试仍安装并执行目标 hook，不全局关闭。
- PATH 替身按用例安装，测试对 PATH 或配置的修改由 monkeypatch 恢复。

种子 fixture 为 session 作用域，不能直接依赖函数级 monkeypatch；使用 session 生命周期受控的环境上下文或向构建 helper 显式传入环境，结束时恢复。所有子进程必须在私有目录清理之前完成；失败时报告明确路径，保留现场供诊断，不触碰真实项目仓库。

## 5. 文件复制、CoW 与存储选择

### 5.1 默认普通复制

首版使用 `shutil.copytree(..., symlinks=True)`，完整复制内容和 `.git`。它不引入新的 Python 依赖，结构可跨平台实现。Windows 的软链能力仍需按现有项目支持范围验证，不能仅因使用标准库就声称原生 Windows 已通过。

副本的 inode 与 ctime 不同，Git index 可能首次查询时重新 stat 或读取内容，这是预期成本。不要通过弱化 `core.checkStat`、mtime 检测或忽略文件模式消除此成本。基准必须包含首次 Git 操作。

Git loose object 本身通常是 `0444`，普通复制会保留只读权限。本次在 macOS 实测原对象与复制对象均为 `0444`，未 chmod 即可用 `shutil.rmtree` 删除副本；POSIX 的删除权限主要取决于父目录，不能将对象只读等同于必然无法清理。

副本保留 Git 原始权限。需要直接改写对象内容的破坏性测试，只对自己副本中的目标对象增加 owner 写权限，再执行被测行为；不递归 chmod 种子或整个副本。Windows 只读属性及 ACL 的清理行为需要独立验证。当前 pytest 9.2.0 的 `rm_rf()` 已有只读权限错误处理和重试，但自定义清理路径不能默认继承该能力，也不能据此声称 Windows 全流程已认证。

### 5.2 APFS CoW 后端

CoW 复制为目标创建独立文件身份，初始数据块共享，后续写入独立。它适合较大文件或数据复制占主要成本的目录；当前约 131 个小文件仍受元数据操作限制，不能预设更快。

可选实现为 macOS `/bin/cp -cR`，或封装受支持的系统 copyfile 能力。先验证种子与目标同卷、软链和权限语义正确，再做性能对比。本机 `man cp` 说明 `-c` 在不支持 clone 时会回退 copyfile，因此仅有退出码 0 不能证明 CoW 已启用。

后端选择在 session 级确定并记录。只有已识别的不支持能力或跨设备条件可以选择普通复制；权限、空间、I/O 错误必须失败，不能以宽泛异常捕获静默回退。普通复制与 CoW 使用同一语义验收，避免维护两套测试构建逻辑。

### 5.3 对象打包与 RAM Disk

种子一次性 repack 可减少 loose object 文件，是否启用由“复制 + 首次查询 + 清理”总耗时决定。仅在独占种子构建阶段执行，完成后验证，再发布；不在源工厂仓执行清理，不使用 aggressive GC。

RAM Disk 仅作为后续可选实验：先证明存储等待占主导，再确认有充足未压缩内存和可控容量。当前内存压缩与 CPU 负载条件下不采用，也不提供未经实测的挂载命令。RAM Disk 不减少进程启动和查询次数，不能用于证明磁盘崩溃持久性。

## 6. 重复 Git I/O 与 CLI 成本优化

### 6.1 按确定 SHA 批量读取

[store.py](../../repo/.repo_template/scripts/repo_task/store.py) 的 `load_task_at_ref()` 调用 `scan_tasks_at_ref()` 扫描全部任务，再读取目标文档；多个生命周期步骤反复调用时形成重复 `ls-tree`、`show` 和 ref 解析。

目标流程：

1. 在明确只读阶段将 ref 解析为确定 commit SHA，保留现有不存在、非法对象与 Git 错误分类。
1. 一次 `ls-tree -r -l -z` 枚举目标树、blob OID 和大小，使用 NUL 分隔处理路径；只收集当前操作需要的文档。
1. 将 OID 请求一次性交给 `subprocess.run(input=..., capture_output=True, timeout=...)`，执行非交互 `cat-file --batch`，由内部 `communicate()` 协调输入、stdout 和 stderr，子进程退出后在内存解析结果。
1. 严格按协议声明字节数解析，处理缺失、类型错误、非零退出及截断输出；不按文本行切分 blob 内容，不把 stderr 混入协议输出。
1. 保留全部 task 记录校验、重复 tid 检查、缺根文档和损坏数据报错；不以只读目标 tid 为由跳过现有全量校验。
1. 将解析后的记录和原文保存在一次操作拥有的快照中，目标文档无需再次读取。

以仓库身份和确定 SHA 作为快照键。解析结果应不可变，或向调用方返回独立可变副本，防止测试或业务代码修改缓存记录。

目标调用形式如下；`root` 为当前操作的仓库目录，`object_ids` 为已核对大小的 blob OID 字节串列表，`timeout` 沿用 Git 操作超时策略。此处是拟实现接口示意，尚未进入产品代码：

```python
result = subprocess.run(
    ["git", "-C", str(root), "cat-file", "--batch"],
    input=b"".join(oid + b"\n" for oid in object_ids),
    capture_output=True,
    timeout=timeout,
)
```

现有 `_git_bytes()` 尚未接收 input，实施时仅增加完成该调用所需的可选 bytes 输入，并维持当前异常转换和调用方错误分类，不另建长连接管理器。`RepoContext` 保持不可变身份，不保存进程、管道或协议游标。多线程和递归调用分别拥有自己的请求、输出与短命进程。

`communicate()` 收集输出到内存，不适用于无界数据。实施时根据目标文档的对象数量与总大小确定有限批次；超出单批预算时拆成多次独立的 run，不改变原有合法数据语义，不将整个仓库内容读入内存。

本次独立机制实验使用 2,000 条 OID 请求，输入 82,000 字节、输出 2,152,000 字节，单次 run 正常完成。该实验验证一次性通信方式可处理超过常见管道容量的数据，不代表已验证产品 reader 的协议解析、超时和错误处理。管道容量并非固定 64 KB；缓冲区满导致阻塞与读取端关闭导致 BrokenPipe 属于不同问题。

### 6.2 快照边界与失效

优先显式传递操作内快照，不建立进程级万能缓存。branch 名、HEAD、工作区内容、ledger 和 worktree 登记均为可变数据，不能直接加全局 lru_cache。

同一 CLI 命令也可能先读取、再提交或创建 worktree。发生写操作后，需要读取新状态的阶段必须新建快照；跨进程可变状态受现有锁和重读约束，不得复用锁外旧视图替代锁内校验。worktree list 仅在明确无变更的阶段内复用，创建、移除、prune 后重新读取。

每批调用均以 run 启动并回收子进程，超时沿用 subprocess.run 的终止和等待处理。禁止自行采用“先写完 stdin，再读 stdout/stderr”的交互循环，也不在一次操作、多个线程或不同用例之间共享管道。只读解析结果可以在明确操作边界内复用，进程句柄不能复用。

### 6.3 测试分层与启动成本

测试安排阶段可用现有领域 API 构造状态，避免每个负面断言都重复运行完整 CLI 链。按以下边界保留真实执行：

|测试目的|执行方式|
|---|---|
|纯解析、图算法、路径分类|内存数据或最小目录 fixture|
|Git 语义、提交血缘、worktree、事务恢复|真实独立仓库，可直接调用领域 API|
|CLI 参数、退出码、stdout/stderr、工作目录定位、跨进程锁|真实子进程|
|完整生命周期与复杂链路回归|保留真实端到端，包括当前最慢用例|

安排阶段优化不应绕过测试正在验证的前置行为。涉及从代码位置解析仓库的场景，不能用主仓已导入模块替代 worktree 内脚本执行；直接调用 API 时必须显式构造正确 RepoContext。

首先审计 `_prepare_done()`、`_cleanup()` 等公共安排 helper 的调用方：若测试只验证后续状态判定，可用显式 RepoContext 调用现有领域 API 并保留真实 Git；若测试验证 CLI、跨进程身份或锁，则继续使用 subprocess。不能让同一个 helper 在调用方不知情时按环境切换执行方式，需要时拆成边界明确的安排 helper 与 CLI helper。

最慢的完整链路回归继续执行其被测命令。优先减少其他测试中重复的非被测安排，再根据端到端耗时评估命令内部 I/O，而不是删除该回归的操作来追求计时下降。P0 记录 Arrange、被测操作和断言的分段成本以及各自子进程数；不得仅凭 pytest call 标签确定改造范围。

[cli.py](../../repo/.repo_template/scripts/repo_task/cli.py) 当前集中导入多个命令模块。按需导入仅在 importtime 证明收益后实施，并补齐所有子命令路由、帮助、错误输出和导入契约验证。不要先引入通用插件框架。

## 7. Git 参数与并发调优

|选项|应用边界与验证要求|
|---|---|
|`gc.auto=0`、`maintenance.auto=false`|可写入临时测试仓配置，减少自动维护；不覆盖专门测试维护语义的场景|
|`core.fsync=none`|仅独占临时仓且不验证持久性时评估；不改用户全局或产品默认配置|
|`core.checkStat=minimal`、关闭模式/ctime 检查|不作为默认优化；会改变快速连续修改、权限等测试语义|
|fsmonitor、untracked cache|小仓默认不额外启用；如评估必须验证缓存失效和后台进程清理|
|Git 可执行入口|运行器统一解析并记录实际路径；复测 Apple shim 成本，不硬编码 Xcode 安装路径到产品|

调度矩阵比较 `-n 2/4/6/8/10` 与 `--dist=load/worksteal`。固定机器与代码版本，按总 wall time 选配置，不能仅按核心数推断最优 worker 数。性能核/能效核差异和其他任务会影响结果。

`loadfile/loadscope` 不是 session fixture 复用的必要条件。大型工作流模块按文件绑到单个 worker 可能增加尾部等待，因此不作为默认选择。`worksteal` 是候选，收益尚未实测。

## 8. 实施工作包与退出条件

各工作包按顺序执行。涉及模板代码时必须完成工厂全量测试；本文只列目标工作，不授权部署、发布或消费仓流程。

|阶段|实施内容|验证与退出条件|
|---|---|---|
|P0：受控基准|记录环境、插件、worker、用例数、阶段耗时与 Trace2；进一步区分 Arrange、被测操作和内部 Git；保留原始数据|相同版本至少三次，记录波动；确定可优化的重复安排和关键路径；干扰明显时不裁决小幅收益|
|P1：Arrange 热点治理|审计公共安排 helper，以显式领域 API 替代非被测前置 CLI；根据 importtime 决定是否按需导入|被测 CLI、跨进程锁和完整链路保留；前置状态等价，实际调用数下降，完整成本单独计量；全量通过|
|P2：单次批量 Git 读取|实现操作内 SHA 快照和有限批次的非交互 run，移除重复扫描；不引入共享管道|保留错误分类与锁内重读；调用数及耗时单独比较；大输入输出、超时和异常回归通过；全量通过|
|P3：独立种子副本|提取经核对的固定 profile，替换重复 fixture，统一配置、权限与资源清理|初始语义一致，隔离与只读对象清理验证通过，初始化次数随 worker/profile 而非用例增长；不把此收益解释为主要 call 优化；全量通过|
|P4：后端与调度|比较普通复制/CoW、打包、Git 入口和 worker 矩阵|按完整成本选择后端与配置，不保留没有收益的优化分支|
|P5：交付记录|记录最终设计、受控前后对比、限制与实际验证|更新必要现行文档和裁决落点，计划状态依据实际结果更新|

预期主要代码落点为工厂 fixture、`repo_task/store.py`、必要的 `git_ops.py` 和调用方。`locks.py`、`context.py` 是边界核对入口，除发现本问题直接相关缺陷外不修改其协议。契约测试随产物分发，性能 harness、fixture 辅助与基准记录保留工厂侧。

## 9. 验收设计

### 9.1 隔离与正确性

需要新增具有独立行为价值的基础设施回归，而不是逐行复刻复制 helper：

- 两个副本同时操作相同 tid 和分支名，各自创建、修改、提交、删除 worktree，互不影响。
- A 修改工作区、Git config、hooks、refs、对象文件与运行态后，B 和种子摘要保持不变。
- 普通文件、`.git/config`、index 及业务锁文件不共享 inode；Git common directory 均位于各自副本；不存在 alternates 或向种子的路径引用。
- A 持有其业务锁时，B 对独立副本的操作能在受控超时内完成；使用独立进程验证，不能只比较锁文件内容。
- 初始 refs、HEAD、提交树、配置、文件模式和必要空目录与原 fixture 一致；种子生成失败不会产生可用副本。
- 验证只读 loose object 的复制与清理；对象破坏测试只 chmod 自有目标文件，种子和其他副本权限不变。Windows 支持状态由实机证据决定。
- 重复启动和失败清理不遗留服务线程、子进程、端口占用与路径污染。
- 单次批量读取覆盖超过常见管道容量的输入和输出、特殊路径、二进制/截断输出、缺失 blob、非零退出、超时回收、并发独立调用、重复 tid、文档损坏、SHA 变化与写入后重读。
- 保留 dirty/untracked/ignored、同长度快速修改、权限、review stale、合并冲突与恢复测试。

串行、至少两个 worker 和最终选定 worker 数均需核对结果。串行可以发现与 worker 数无关的同进程状态污染，多 worker 验证跨进程并发边界。

### 9.2 性能指标

|指标|验收口径|
|---|---|
|全量正确性|同一基线用例与关键断言保留；新增基础设施测试单独统计，不以减少用例换速度|
|种子初始化次数|每个实际使用的 profile 每个 worker 至多一次；复制阶段不执行 init/config/add/首次 commit|
|Arrange 收益|在同一测试目标下记录安排 helper 的子进程数与耗时下降；关键 CLI、锁与完整链路覆盖保持不变|
|fixture 收益|记录构建、复制、首次 Git 查询与清理的合计成本；单独报告累计经过时间和 wall time，不当作 CPU 时间|
|Git I/O 收益|同场景比较调用数与完整耗时；按步骤解释移除的重复调用与保留的必要调用，不预设 40% 降幅|
|全量 wall time|受控 A/B 至少三次，报告中位数、范围和 worker 配置；只有收益超过观测波动才认定加速，不预设 30% 或直接对照受干扰的 240.05 秒|
|CoW / pack / 参数|只保留总收益超过运行波动、且隔离和正确性全部通过的选项|
|清理与资源|单独报告 teardown 和全量结束清理成本；无后台资源泄漏或随重复运行增长|

不预设“必达两分钟”，也不为各阶段设置缺乏预算推导的固定降幅。每个工作包先比较同一受控基线，再记录组合效果，不能简单相加。当测量噪声大于候选方案收益时，结论记为未确认，先恢复受控条件再裁决；只有结构改善而无可确认提速时，应如实区分两类结果。

## 10. 基准执行与复现协议

### 10.1 前置条件与记录

工作目录为工厂根，pytest 环境安装 pytest-xdist；使用同一 Python、Git、插件集合和源码版本。当前已验证 pytest 工具环境与默认 `python3` 不同，默认 `python3 -m pytest` 不可假定可用。

每轮记录 commit、工作区差异、收集数量、运行时间、worker 数与调度器、Git 实际路径、CPU/内存压力、是否启用 Trace2、缓存冷热条件。只记录必要环境信息，不导出完整环境变量或全部 Git 配置，以免包含凭据。

所有 `--basetemp` 使用 `mktemp -d` 生成的系统临时目录。运行会创建和修改测试私有仓库；任一失败先核查实际状态并保留诊断，再决定重跑，不能将失败运行纳入速度收益。

### 10.2 正确性与阶段耗时

以下全量命令已在分析阶段运行通过；实施完成后必须重新执行，其历史通过结果不能替代新代码门禁：

```bash
pytest repo/.repo_template/tests factory_tests -q -n auto \
  -p no:cacheprovider --basetemp "$(mktemp -d)" --durations=25
```

以下是后续对照命令，参数已按 pytest/xdist 接口核对，本轮没有执行该组合。成功条件是全量通过并记录 wall time；失败时停止性能比较：

```bash
pytest repo/.repo_template/tests factory_tests -q -n 4 --dist=worksteal \
  -p no:cacheprovider --basetemp "$(mktemp -d)" --durations=25
```

串行核对沿用相同命令但显式 `-n 0`。每种候选组合至少三次，交错或随机排列运行顺序，报告中位数和范围，避免按时间批次混入主机负载变化。标准正确性门禁和性能矩阵分别记录，调度实验不擅自修改当前标准命令。

### 10.3 归因工具

后续在工厂侧实现轻量观测辅助：

- 通过 `pytest_runtest_logreport` 按 worker 汇总 setup/call/teardown；跨 worker duration 累加是累计经过时间，不能当 CPU 时间或整套 wall time。
- 对高频 Arrange helper 与被测 CLI 分别记录调用数和经过时间；父子调用耗时有包含关系，不能重复相加或推断 CPU 时间。
- `subprocess.run` 包装只统计本进程调用，使用受控上下文恢复；不误称其覆盖所有子进程层级。
- Git Trace2 输出使用每次运行独立目标，按 session ID 和 command name 统计；避免固定路径追加混入旧数据。
- `python -X importtime` 必须由 pytest 所用解释器执行，单独测量 CLI 导入；区分首次源码编译与后续启动。
- 最终性能比较关闭 Trace2 和额外 hook，归因运行与无观测运行分开。

复制微基准覆盖：源文件生成、Git 初始化、首次提交、复制、首次 status/读对象、真实 worktree add/remove、目录清理。比较相同初始内容、对象布局和源/目标文件系统，禁止只测复制主体就宣布全量收益。

## 11. 风险、停止条件与恢复

|风险|停止条件|处理|
|---|---|---|
|种子泄漏绝对路径或共享 Git 状态|common dir、alternates、config、软链指向其他副本或种子|停止该 profile，修复构建契约并补隔离回归；不增加 reset 清场补丁|
|snapshot 陈旧|同一操作写入后仍读旧 refs/ledger/worktree，或锁内复用锁外数据|缩小快照生命周期，恢复必要重读，保留失败案例|
|批量 reader 弱化 fail-closed|错误被当成空列表/缺任务，或丢失完整记录校验|停止替换，修复错误分类和校验，再跑全量|
|性能噪声|CPU/内存压力、用例数、插件或版本不一致|保留数据但不裁决收益，恢复一致条件后重测|
|CoW / pack 无总体收益|收益落在波动范围或首次读取/清理抵消收益|选择普通复制，移除无收益的候选复杂度|
|配置调优改变测试语义|hook、权限、快速修改、签名或配置测试行为变化|收窄配置到明确场景；不修改断言掩盖偏差|

改造按工作包保持可审阅边界。出现问题时保留失败现场，只修复或撤回本次对应变更，不清理真实仓库、不回退用户其他修改。种子位于本次临时目录，失败后可以重新生成；不设计跨版本旧缓存兼容层。

## 12. 来源与待验证范围

源码入口以分析基线为准：[工作流 fixture](../../factory_tests/test_dispatch_integration.py)、[同步 fixture](../../factory_tests/test_repo_sync.py)、[RepoContext](../../repo/.repo_template/scripts/repo_task/context.py)、[锁](../../repo/.repo_template/scripts/repo_task/locks.py)、[task 存储读取](../../repo/.repo_template/scripts/repo_task/store.py)、[Git 操作](../../repo/.repo_template/scripts/repo_task/git_ops.py)、[CLI 导入](../../repo/.repo_template/scripts/repo_task/cli.py)。

外部语义在分析阶段核对过以下官方资料，实施时需同时核对目标安装版本：

- [Git worktree](https://git-scm.com/docs/git-worktree)：共享 refs/config 与每 worktree 状态边界。
- [Git clone](https://git-scm.com/docs/git-clone)：local clone、硬链接、shared/alternates 和 dissociate。
- [Git config](https://git-scm.com/docs/git-config)：fsync、自动维护、文件状态检测配置。
- [Python subprocess](https://docs.python.org/3.14/library/subprocess.html)：run 的 input/capture_output、communicate、超时回收与内存边界。
- [Python shutil](https://docs.python.org/3.14/library/shutil.html)：复制权限和 Windows 只读目录清理示例；pytest 清理能力另已核对本机 9.2.0 的 `_pytest.pathlib.rm_rf()` 与 `on_rm_rf_error()` 实现。
- [pytest-xdist fixture how-to](https://pytest-xdist.readthedocs.io/en/stable/how-to.html)：session fixture 按 worker 执行及跨进程共享初始化模式。
- [pytest-xdist distribution](https://pytest-xdist.readthedocs.io/en/latest/distribution.html)：auto worker 与 load/worksteal 等调度方式。
- [Apple APFS](https://developer.apple.com/documentation/foundation/about-apple-file-system)：同卷 clone；`cp -c` 的回退语义另已核对本机 `man cp`，目录 clone 限制已核对 `man clonefile`。

尚未验证：优化实现后的任何加速倍数、空闲主机 worker 最优值、CoW 的稳定总体收益、pack 策略收益、RAM Disk、原生 Windows 全流程，以及按需导入收益。它们不构成已经实现的能力，需按对应工作包补证据。
