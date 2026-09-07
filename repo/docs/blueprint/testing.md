# 测试

模板仓默认门禁命令（复制新项目后按实际工具链替换）。章节名 `doctor_cmd` / `test_cmd` / `blackbox_verify` 是 preflight 与 skill 的机械锚点，不得改名；命令写在对应章节正文。未使用某类时章节保留，正文写「无」。

下列默认命令在消费仓根（工厂验产物时为 `repo/`）的 POSIX shell（Linux/macOS/WSL/Git Bash）执行，需要 Python 3、PATH 中可运行的 pytest（及其所属 Python 环境）、Node.js（测试中的 JS 场景）和 PATH 中的 md_kx。任一检查非零退出则停止后续验收，先区分「环境缺工具」与「shell 不兼容」（Windows 原生 shell 无 `command -v`），修复环境或测试后重跑；收集成功只证明可收集，不等于测试通过。

## doctor_cmd

环境前置检查：测试可成功收集；另需 `md_kx` 在 PATH（Markdown 格式化，见 `.repo_template/scripts/md_format.py`）。

```bash
pytest .repo_template/tests -q --collect-only
command -v md_kx
```

## test_cmd

日常测试（红/绿）：

```bash
pytest .repo_template/tests -q
```

填本命令时按「门禁类别清单」逐类覆盖；项目不适用某类写「无」并说明理由。运行时通过 ≠ 类型 / 构建正确，每类须有独立验证。

## blackbox_verify

无

模板默认不提供独立的业务黑盒验收命令；工具链 CLI/Git 场景由 `.repo_template/tests/` 覆盖。消费项目应补充自身的用户可观察行为验证，不能用模板测试代替业务验收。

## Schema / codegen 验证

项目使用 schema、migration 或 codegen 时，维护以下内容；未使用时明确写「无」。

- 触发路径：会触发生成或兼容性检查的文件、目录或变更类型
- 生成命令：本地生成派生产物的命令
- 验证命令：验证生成结果与运行时兼容性的命令
- 合并后动作：合入默认分支后必须执行的本地动作及验证
- migration 窗口：是否需要串行或独占窗口；涉及数据迁移时写明环境、审批与执行入口

普通 merge 不自动执行生产 migration、部署或数据操作；此类动作遵循项目发布流程。

## 门禁类别清单

|类别|必须覆盖|常见盲区|
|---|---|---|
|单元测试|单测框架运行通过|mock 掉被测逻辑、断言过弱（假绿）|
|生产代码类型检查|全仓静态类型检查通过|类型错误被测试框架转译忽略|
|测试代码类型检查|测试文件独立类型检查通过|测试 mock 类型不匹配、长期积累无人修|
|lint|全仓静态规则通过，或与基线 diff 无新增违规|只查改动文件、存量无限积累|
|生产构建|生产编译 / 构建通过|codegen 与 schema 不同步、RSC 边界、server-only 导入|
