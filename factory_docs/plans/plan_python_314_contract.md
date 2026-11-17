# 模板工具链 Python 3.14.7 契约与彻底重构（Issue #97）

> 编写时间：2026-11-16T19:11:38+08:00
>
> 修订时间：2026-11-17T10:44:26+08:00
>
> 关联 Issue：`TuTouPower/repo_template#97`
>
> 目标版本：Python 3.14.7
>
> 决策状态：Accepted（用户 2026-11-16 批准）
>
> 实现状态：Implemented（2026-11-16 落地）
>
> 验证状态：Verified（审查复核后：契约子集 385 passed；工厂全量 1674 passed）
>
> 权威落点：本方案定义目标契约。落地后契约权威为 `repo/.repo_template/scripts/_runtime.py` 的 `PYTHON_VERSION`；用法权威为 `repo/.repo_template/docs/usage.md`；工厂门禁权威为工厂根 `AGENTS.md`。

## 1. 目标与成功标准

### 1.1 目标

模板工具链统一以 Python 3.14.7 为唯一契约：分发产物 `repo/`、工厂测试 `factory_tests/`、工厂根配置中的版本声明与代码约束一致；清除所有为兼容 Python 3.9 而存在的取舍代码与检查，使「契约、验证环境、开发解释器」三者同一。

### 1.2 成功标准

|编号|判据|验证方式|
|---|---|---|
|AC-1|`_runtime.py` 定义唯一 `PYTHON_VERSION = "3.14.7"`；所有 Python 入口启动前校验，低于则明确中文提示并 `sys.exit(2)`|契约测试 + 用 3.9.6 调用入口观察提示|
|AC-2|全仓无 `from __future__ import annotations`；`"ctx.RepoContext"` 等字符串注解改为直接类型引用|全仓计数为 0 + 全量测试|
|AC-3|14 个 CLI/模块 `__main__` 与 3 个 hook 统一经 `_runtime.run()` 启动；`reconfigure` 样板收敛到一处；模块顶部 `sys.path.insert` 作为导入依赖保留|逐个 `--help` 冒烟 + hook 临时仓试跑|
|AC-4|`export_template.py` 保留手写受控解包：软链按原始 `linkname` 写入、以 resolve 后真实路径校验越界（`data_filter` 会规范化 linkname，不用于落盘）；业务白名单与 staging 原子重命名保留|六类归档安全用例 + `dir/../file` 软链语义回归|
|AC-5|所有现行版本声明为 3.14.7；无现行文档仍写 3.9 为最低版本|契约测试断言三处文档 == `PYTHON_VERSION`|
|AC-6|删除 3.9 专项兼容测试与注解静态检查|全量收集结果不含这些用例|
|AC-7|契约子集与工厂全量测试全绿|§7|

### 1.3 不在范围

- 消费仓业务代码与消费仓自有文档：由 `repo-template-sync` 拉取本修复。
- `factory_docs/archive/`、`reviews/`、`audits/`、`decision_log.md` 历史行：保留历史记录，由工厂根 `AGENTS.md` 声明 3.9 约束作废。
- `pending.py` / `spikes.py` 的 6 处 PEP 701 f-string：3.14 合规，是契约失真而非代码错误，不修改。
- §5 B 类代码：经甄别非版本取舍，逻辑不动。
- 提交与推送：完成报告后经用户同意另行处理。

## 2. 当前状态与根因

|事实|证据|
|---|---|
|默认解释器与全量门禁均为 3.14.7|`python3 --version` = 3.14.7；`pytest` 为 uv tool 独立环境 `/Users/karson/.local/share/uv/tools/pytest/bin/python`（3.14.7，pytest 9.2.0 + xdist 3.8.0）|
|现行契约声明 3.9|`repo/README.md:11`、`repo/.repo_template/docs/usage.md:137/167`|
|3.9.6 下两个 CLI 解析失败|`/usr/bin/python3`（3.9.6）执行 `pending.py list`、`py_compile spikes.py` 均报 `SyntaxError: f-string: unmatched '('`|
|6 处 PEP 701 f-string|`pending.py:280/282/351`、`spikes.py:82/87/115`；3.14.7 与 3.12.14 对 `scripts/` 75 个 `.py` 全量 compile 均零失败|
|当年为 3.9 做的取舍清单|`factory_docs/decision_log.md:152`：移除 `tarfile.extractall(filter=...)`、联合注解延迟求值、替换 `Path.write_text(newline=)`、md_kx 独立工具环境|
|为 3.9 而写的检查代码|`test_contract_suite.py:152-189`、`test_precommit_hook.py:209-231`、`test_review_report_regressions.py:228/250`|
|3.14 PEP 649 语义|3.14 延迟注解在访问 `__annotations__` 时求值，与 `from __future__` 字符串化不同；全仓无 `get_type_hints` / `__annotations__` 依赖|

根因不是「6 行语法写错」，而是**「最低 Python 版本」没有机器可执行的单一真相源，且验证环境与声明契约脱节**：契约只存在于文档、一条 3.9 静态检查、全仓 future 样板与隐性语法约束中；工厂全量测试跑在 3.14.7，从未在声明的 3.9 上执行；13 个 CLI 入口各自复制启动样板，横切约束无收敛点。3.12+ 语法因此无声进入产物并分发。

## 3. 目标状态（与现状差异）

|维度|当前|目标|
|---|---|---|
|版本契约|文档散写 3.9，代码无声明|`_runtime.py` 单一常量 `PYTHON_VERSION = "3.14.7"`|
|入口启动|12 顶层 CLI + 2 包内子模块 + 3 hooks 各自 `reconfigure` / 四种 `__main__` 形态|17 个 `__main__` 统一 `run(main)`；`reconfigure` 收敛到 `_runtime`；模块顶部 `sys.path.insert` 作为导入依赖保留|
|注解|约 104 处 future + 字符串注解|无 future，直接类型引用（PEP 649）|
|归档解包|50 行手写路径/类型/软链/权限校验|保留手写解包（链接语义正确性要求，非 3.9 包袱）；补 `dir/../file` 软链语义回归|
|门禁|3.9 静态检查与专项测试|3.14.7 契约断言 + 文档一致性 + 全量真实解释器验证|
|文档|3 处现行 3.9 声明|3 处 3.14.7 声明；历史归档保留并标注作废|

## 4. 设计

### 4.1 单一真相源

新增 `repo/.repo_template/scripts/_runtime.py`：

- `PYTHON_VERSION = "3.14.7"`，解析为 `MIN_PYTHON = (3, 14, 7)`。
- `require_python()`：`sys.version_info[:3] < MIN_PYTHON` 时向 stderr 输出「当前版本 / 要求版本 / 升级指引」并 `sys.exit(2)`。
- `run(main)`：按顺序完成 `sys.path` 注入（保证同目录 `repo_task/` / `template_sync/` 可导入）、stdout/stderr UTF-8 `reconfigure`（保留 `hasattr` 判断，兼容 stdout 被替换）、`require_python()`、调用 `main()`、`raise SystemExit(code)` 统一返回码。
- 模块导入时自动把 `scripts/` 注入 `sys.path`；`hooks/` 入口沿用既有 `TOOLKIT_ROOT` path 注入后导入 `_runtime`。

### 4.2 统一启动层

17 个 `__main__` 入口收敛为块内 `from _runtime import run` + `run(main)`：12 个顶层 CLI（`export_template.py`、`findings.py`、`md_format.py`、`pending.py`、`render_review_prompts.py`、`repo_cleanup.py`、`repo_state.py`、`repo_sync.py`、`skill_registry.py`、`spikes.py`、`task.py`、`track_worktree.py`）、2 个包内子模块（`repo_task/review_status.py`、`repo_task/statusline.py`）、3 个 hook（`pre-commit`、`commit-msg`；`pre-merge-commit` 为指向 `pre-commit` 的软链）。包内子模块与 hooks 因目录层级不同，`__main__` 块内保留一行 `scripts/` 路径注入。

模块顶部既有 `sys.path.insert` 是导入依赖（保证 `repo_task/` / `template_sync/` 可导入），保留不动；`_runtime.py` 导入时的自动注入作为冗余保险。`repo_task/lifecycle.py:51` 的 `except ImportError` 兜底同为导入路径容错，保留；函数内部动态路径处理（`:54` 子进程场景）不动。

### 4.3 3.14 语法升级

- 删除全仓 `from __future__ import annotations`（产物树 scripts 62 文件、tests 9 文件、factory_tests 30 文件、hooks 3 文件）。
- 字符串注解（`"ctx.RepoContext"`、`"CandidateIdentity"` 等）改为直接类型引用。
- `export_template.py`：`_extract_product` 保留手写受控解包——先校验全部成员，再写普通文件/目录，最后按原始 `item.linkname` 写软链，并以 `resolve()` 后的真实路径校验越界；绝对路径、Windows 驱动器/反斜杠路径、`..`、重复条目、非目录/文件/软链类型显式拒绝；`is_non_distributable` 白名单与 `finally: shutil.rmtree(staging)` 保留。审查复核确认 `tarfile.data_filter` 会把 `dir/../file` 规范化为 `file`，改变经软链解析后的实际目标，故不用于落盘。
- 改写 `factory_tests/review/test_review_report_regressions.py` 的两个解包测试：去掉 `python39` 命名，保留 `ValueError` + target 为空 + 可执行位/内部软链断言（对外错误契约不变）。

### 4.4 文档与门禁

- 现行声明统一 3.14.7：`repo/README.md:11`、`usage.md:137/167`、`repo/docs/blueprint/testing.md:9`、`export_template.py:42` docstring、`test_review_report_regressions.py:1` docstring、`factory_docs/plans/plan_global_id_uniqueness.md:309`。
- 措辞更新（逻辑不动）：`md_format.py:4`（md_kx 自身 tool 环境门槛，非模板契约）、`worktrees.py:269`（3.14 下 rglob 仍跳过 dangling symlink）。
- `testing.md` 的 `doctor_cmd` 增加一行 `python3 -c` 版本前置检查；`test_contract_suite.py` 现有 doctor 断言只取 `pytest` 行与 `command -v md_kx`，不受影响。
- 契约测试：删 `test_toolchain_annotations_importable_on_python39` 与 `_runtime_union_annotations`；`test_contract_suite_runs_on_contract_python` 只校验运行期版本。模板产物树 README / usage / testing 的版本声明一致性属工厂侧，落在 `factory_tests/integration/test_python_version.py`（消费仓 README 由项目定制，不校验）。`CONTRACT_MODULES` 白名单不变。
- 工厂层：新增断言 `sys.version_info[:3] >= MIN_PYTHON`，使「契约 = 门禁环境」成为可执行约束。
- 删 `factory_tests/review/test_precommit_hook.py:209-231`。
- 工厂根 `AGENTS.md`：加权威声明「工具链 Python 3.14.7；`factory_docs/archive|reviews|audits` 中 3.9 约束为历史记录，已作废」。
- `factory_docs/decision_log.md`：按文件内既有惯例（「原条目不改写、保留历史记录」）追加新决策节，不改写历史行。

## 5. 现状盘点

### 5.1 A 类：确属 3.9 取舍，清除

|取舍|落点|
|---|---|

|future 样板与字符串注解|全仓约 104 处|
|3.9 注解静态检查|`test_contract_suite.py:152-189`|
|3.9 专项兼容测试|`test_precommit_hook.py:209-231`|
|3.9 版本声明|`README.md:11`、`usage.md:137/167`、`testing.md:9`、`test_contract_suite.py:174/189`、`plan_global_id_uniqueness.md:309` 等|

### 5.2 B 类：甄别为非版本取舍，逻辑不动

|代码|判定|
|---|---|
|`md_format.py:4` 选文件/黑名单|md_kx 为 uv tool 独立环境，门槛指 md_kx 自身；仓内选文件是业务需求|
|`worktrees.py:269` os.walk|3.14 下 `rglob` 仍跳过 dangling symlink，清理需删除残留，os.walk 仍必要|
|`documents.py:143` `open(fd, ..., newline="\n")`|原子写（O_EXCL / fsync / os.replace）必须用 fdopen；`open()` 的 newline 3.9 即支持，`Path.write_text(newline=)` 不提供原子语义|
|`locks.py:34` `hasattr(os,"register_at_fork")`|Windows 平台兼容|
|11 处 `hasattr(sys.stdout,"reconfigure")`|stdout 被 capsys / 重定向替换时的保护；收敛到一处但保留判断|
|`lifecycle.py:644` ImportError 兜底、`os.name == "nt"`、`typing.Any/NamedTuple/Callable`|导入路径容错、平台分支、3.14 仍合法的类型导入|
|`export_template.py` 手写受控解包|`data_filter` 会规范化 linkname 改变软链解析目标；保留原始 `linkname` 写入与 resolve 边界校验，属链接语义正确性要求，非 3.9 包袱|

## 6. 执行步骤

|步|动作|失败处理|
|---|---|---|
|1|新增 `_runtime.py`|语法自检；导入失败即停|
|2|批量删除全仓 future 与字符串注解|逐文件 compile 校验；循环导入导致注解求值 NameError 时修导入结构，不回退 future|
|3|收敛 14 个 CLI/模块 `__main__` 与 3 个 hook|逐个入口 `--help` 冒烟；失败回查该入口形态|
|4|审查复核后恢复 `export_template.py` 手写解包（软链语义）+ 补 `dir/../file` 回归|六类安全用例与软链语义用例必须全绿|
|5|删 3.9 专项测试与注解静态检查|收集结果核对无残留|
|6|统一文档版本声明、措辞、doctor_cmd|契约测试断言文档 == `PYTHON_VERSION`|
|7|新增契约断言与工厂层版本断言；AGENTS.md / decision_log.md / plans 更新|契约子集先绿|
|8|全量验证（§7）|失败修到绿，不提交|

## 7. 验证

在工厂根 `/Users/karson/kar/code/repo_template` 执行，前置：PATH 中 `python3` 3.14.7、`pytest` 9.2.0、`pytest-xdist` 3.8.0、`md_kx`。

```bash
pytest repo/.repo_template/tests -q -m contract
pytest repo/.repo_template/tests factory_tests -q -n auto -p no:cacheprovider --basetemp "$(mktemp -d)"
```

复核实测：契约子集 385 passed（30.50s）；工厂全量 1674 passed（535.74s，Python 3.14.7 / pytest 9.2.0 / pytest-xdist 3.8.0）。

补充验证：

- 14 个 CLI/模块 + 3 个 hook 逐个 `--help` / 冒烟。
- `/usr/bin/python3`（3.9.6）与 3.12.14 调用任意 CLI 入口，均应看到明确版本提示并 exit 2（检查在业务 import 之前）。
- `export_template` 六类归档安全用例：traversal / absolute / nested_drive / symlink_parent / hardlink / device，加可执行位、内部软链与 `dir/../file` 原始 linkname 语义保留。
- future 全删后重点回归：注解求值、NamedTuple / dataclass、循环导入模块（`repo_task/store.py`、`template_sync/duplicate_ids_migration.py`）。
- `grep -rn 'from __future__ import annotations'` 与 `grep -rn 'Python 3.9'`（现行文档）计数为 0。

## 8. 风险与回滚

|风险|缓解|
|---|---|
|PEP 649 语义变更：3.14 延迟注解在访问 `__annotations__` 时求值，与 future 字符串化不同|全仓无 `get_type_hints` / `__annotations__` 依赖；全量测试兜底；个别循环导入模块若求值期 NameError，修导入结构|
|`data_filter` 规范化 linkname 改变软链解析目标|不用于落盘；软链按原始 `linkname` 写入，以 resolve 后真实路径校验越界；`dir/../file` 回归覆盖|
|入口在低版本解析/导入期即失败|`require_python()` 前移到入口顶部、业务 import 之前；6 处 PEP 701 f-string 改低版本可解析，保证引导入口在 3.9 可解析|
|改动面约 110 文件|分批落地、每批跑契约子集、末次全量；失败按步回退，全部改动未提交前可用 `git diff` 复核|

回滚：所有改动均为工作区未提交修改，按文件逐步恢复即可；不执行 `git reset --hard` 或批量删除。

## 9. 取舍记录

|议题|结论|理由|
|---|---|---|
|最低版本 3.14.7 而非 3.12|3.14.7|本机默认解释器与全量门禁已是 3.14.7，契约与验证环境天然同一，零环境改动；3.12 方案需另装 pytest/xdist 并改门禁命令|
|精确 `3.14.7` vs `3.14+`|精确 `3.14.7`|按用户指定；如需容忍 3.14.x 其他补丁，`require_python` 改判 `>= (3,14)`|
|启用 md_kx `exclude`|否|消费仓 md_kx 由各自 tool 环境决定，不可假设；保留仓内选文件/黑名单|
|`typing` → `collections.abc` 风格迁移|否|与 3.9 无关，避免顺手改进|
|历史归档中的 3.9 约束|保留历史、AGENTS.md 声明作废|承担审计与追溯价值，不静默覆写|

## 10. 回执与交付

- Issue #97 追加评论：根因、改动文件、测试结果、消费仓同步提示；修复已验证且用户确认后再关闭。
- 完成报告列改动文件、已执行/未执行的检查、未解决冲突与开放项。
- 提交走正常门禁：列改动文件、用户同意后提交，不 push。
- 消费仓通过 `repo-template-sync` 拉取修复。


## 11. 审查复核修订（2026-11-17T10:44:26+08:00）

|问题|结论|修复|
|---|---|---|
|P1 README 定制阻断消费仓门禁|`repo/README.md` 是消费仓项目文档（初始化指南要求替换），不是模板契约文档；版本一致性校验属工厂侧|契约测试只校验运行期版本；模板产物树 README / usage / testing 的版本声明一致性迁至 `factory_tests/integration/test_python_version.py`|
|P2 导出悄然改变软链指向|实测 `tarfile.data_filter` 将 `dir/../file` 规范化为 `file`，改变经软链解析后的实际目标；手写解包是链接语义正确性要求，非 3.9 包袱|恢复 `_extract_product` 手写解包：软链按原始 `linkname` 写入、resolve 后真实路径校验越界；`data_filter` 不用于落盘；补 `test_extract_preserves_symlink_target_semantics`|
|P3 版本检查晚于业务解析与导入|`require_python()` 原在文件尾 `run()` 内，低版本在解析/导入阶段即失败|16 个入口在文件顶部（业务 import 之前）调用 `require_python()`；6 处 PEP 701 f-string 改低版本可解析写法；3.9.6 / 3.12.14 实测明确提示并 exit 2|
