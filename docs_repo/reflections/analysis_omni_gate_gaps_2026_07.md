# 门禁缺口分析：typecheck:test / build / lint 未全覆盖（2026-07）

## 背景

审计 omni_media 项目 150 个归档 task 的 `task.md` 后发现：`npm run typecheck:test`（测试代码类型检查）当前失败，涉及十几个测试文件、数十处类型错误，且长期未被发现。根因是红/绿门禁未覆盖测试代码类型检查。

延伸排查发现 `build` 与 `lint` 同样存在门禁覆盖缺口。本文记录实证与推荐。

实证数据（2026-07-29 于 omni_media 主仓 main 分支实跑）：

|命令|当前状态|是否在红/绿门禁|
|---|---|---|
|`npm test`（vitest）|通过|是|
|`npm run typecheck`（生产代码 tsc）|通过|是|
|`npm run typecheck:test`（测试代码 tsc）|**失败**（十几个文件数十处错误）|**否**|
|`npm run lint`（eslint 全仓）|**15857 problems**（1225 errors / 14632 warnings）|**否**|
|`npm run build`（next build + prisma generate）|通过|**否**（task 自觉跑，非门禁）|

## 缺口 1：测试代码不类型检查（typecheck:test）

### 碰到的问题

`test_cmd` 原定义：

```bash
npm test              # vitest 单测
npm run typecheck     # tsc --noEmit
npm run smoke:*       # 按需
```

`npm test` 用 vitest，vitest 运行时不做类型检查（esbuild 转译忽略类型错误）。`npm run typecheck`（`tsc --noEmit`）只查 `tsconfig.json`，不含 `tests/`。`npm run typecheck:test`（`tsc --noEmit -p tsconfig.test.json`）定义在 package.json 但不在任何门禁里，从不跑。

### 实证

`npm run typecheck:test` 当前失败，错误集中在：

|文件|错误数|主要类别|
|---|---|---|
|`tests/unit/xhs/tikhub_client.test.ts`|17|`FetchLike` mock 缺 `ok/statusText/body` 字段|
|`tests/unit/t071_rate_limit_hardening.test.ts`|12|速率限制 mock 类型不匹配|
|`tests/unit/xhs/parse_note.test.ts`|9|mock 类型|
|`tests/unit/xhs/blogger_fetch.test.ts`|9|mock 类型|
|`tests/unit/t031_logger.test.ts`|9|logger mock 类型|
|`tests/unit/api/admin_billing.test.ts`|9|mock 类型|
|`tests/e2e/t130_anonymous_parse.e2e.ts`|7|e2e 类型|
|其余十几个文件|各 2-5|`NODE_ENV` 只读赋值、BigInt 等|

### 后果

- 测试文件类型错误长期积累，无 task 修复
- 任何改测试的 task 都在坏基线上工作，新增类型错误被淹没
- t073、t115 等多个 task 在 task.md 记录了「typecheck:test 失败」但未作为阻断项，直接收尾——agent 看到了但无感，因为不在通过条件里

### 根因

门禁定义（`test_cmd`）遗漏 `typecheck:test`；vitest 运行时通过给出虚假绿灯。

## 缺口 2：build 不作为红/绿门禁

### 碰到的问题

`tasks-run` Step 3 绿通过条件引用 `{test_cmd}`，原 `test_cmd` 不含 `npm run build`。build 会触发 `prisma generate`（codegen）+ Next.js 生产编译，能暴露运行时类型与 codegen 问题。agent 可能只跑 test + typecheck 就判绿，跳过 build。

### 实证

- 150 个归档 task 中，约 65 个 task.md 提到 `npm run build`，其余 85 个未提（可能跑过未记，也可能跳过）
- t001 task.md 明确把 build 作为「绿」的回归门禁（自觉补上），说明 agent 自己意识到 test_cmd 不够，但这是个案非规则
- t080/t076/t088 出现 worktree 下 build 失败（Turbopack root 推断到外层仓）后改主仓补做的情况，build 未在门禁导致这些问题在收尾时才暴露
- 当前 main 分支 `npm run build` 通过，但这是显式实跑确认，非门禁保证

### 后果

- codegen 产物与 schema 不同步（prisma generate 未跑）的 task 可能混过绿阶段
- Next.js 生产构建特有问题（RSC 边界、server-only 导入、dynamic route 类型）在黑盒阶段才暴露，修复成本高
- worktree 下 build 环境差异导致黑盒推迟，部分 task（t080/t088）黑盒未跑完就收尾

### 根因

`test_cmd` 未列 build；tasks-run 绿阶段未强制 build。

## 缺口 3：eslint 不在门禁

### 碰到的问题

eslint 从未进入红/绿门禁。task.md 中仅 9 个提到 eslint/lint，且多为 agent 自觉跑「修改文件无错」（t115）或「新增代码 lint-clean」（t074），不是全仓门禁。

### 实证

`npm run lint` 当前：**15857 problems**（1225 errors / 14632 warnings）。其中 475 errors 可 `--fix` 修复。

关键记录：

- t029 task.md：「`npm run lint` 失败，项目存量 16 个 error；本 task 前已有，未扩大」
- t074 task.md：「`npm run lint` 在 main 基线即有项目级违规，本 task 新增代码 lint-clean」
- t076 task.md：「既有全库 `npm run lint` 存在大量与本 task 无关的既有失败，未在本 task 修改」
- t084 曾一次性清理 4828 个 error（主要为缩进），但后续又积累

### 后果

- 存量 lint 错误持续积累，t084 清理后回潮
- agent 普遍采用「只查改动文件」策略，全仓基线无人维护
- 新增代码的 lint 违规被存量淹没，reviewer 难判哪些是新引入

### 根因

`test_cmd` 未列 lint；无 lint 基线快照机制（存量与新增无法区分）；agent 自觉跑但不强制。

## 推荐方案

### 方案 A：test_cmd 全覆盖（最小必要）

`test_cmd` 改为：

```bash
npm test                    # vitest 单测
npm run typecheck           # 生产代码 tsc
npm run typecheck:test      # 测试代码 tsc（新增门禁）
npm run lint                # eslint 全仓（新增门禁）
npm run build               # 生产 build（新增门禁）
npm run smoke:*             # 按需
```

红/绿阶段全部通过才能判绿。tasks-run Step 3 引用 `{test_cmd}` 自动覆盖。

**优点**：改一处（testing.md），tasks-run 自动生效；门禁定义集中。
**风险**：存量基线（typecheck:test 失败 + lint 15857 问题）未修前，所有 task 都跑不过门禁。需先修存量或设基线快照。

### 方案 B：lint 基线快照（应对存量）

lint 不强制全过，改为「不得新增」：

- 维护一份 `.lint-baseline`（当前存量违规清单，格式 lint 支持）
- 门禁跑 `npm run lint` 后与 baseline diff，新增违规才 FAIL
- 存量逐步清理，每清一批更新 baseline

**优点**：不阻塞当前 task；新增违规可控。
**风险**：baseline 维护成本；需要 lint 工具支持 baseline diff（eslint 可用 `--output-file` + 自定义 diff 脚本）。

### 方案 C：build 强制 + worktree 环境修复

build 作为门禁，但需先解决 worktree 下 build 环境问题：

- Turbopack root 推断：worktree 在仓库外导致 root 误判（t080/t088 实证）
- 方案：worktree 内 `NEXT_DIST_DIR` 隔离构建产物（t129 已部分做）+ 文档说明 worktree build 注意事项

**优点**：暴露 codegen 与生产构建问题。
**风险**：worktree 环境差异导致 build 失败非代码原因；需环境侧配套。

## 与现有裁决的关系

|裁决|关系|
|---|---|
|L11（TDD 顺序违规）|互补。L11 管「改测试适配实现」的方向；本议题管「测试代码本身类型正确」|
|L2（finding 锚 AC）|互补。typecheck:test 失败是可观测行为缺陷，reviewer 应出 finding，但门禁缺位致 reviewer 也看不到|
|L14（一 task 一 commit）|相关。build/lint 缺门禁时，task 收尾质量靠 agent 自觉，与 L14「commit 须可独立验证」矛盾|

## 落地建议

1. **立即**：`test_cmd` 加 `typecheck:test`（方案 A，已实施于 omni_media）
2. **立即**：`test_cmd` 加 `build` + `lint`（方案 A，已实施于 omni_media）
3. **建 task**：修 typecheck:test 存量错误（omni_media 已登记 f043，important）
4. **评估**：lint 存量是否走基线快照（方案 B）还是一次性清理（t084 先例：曾清 4828 个，但回潮）
5. **评估**：worktree build 环境修复（方案 C）是否进模板

## 开放问题

- lint 全仓门禁对 commit 耗言的影响（15857 problems 全跑可能慢）
- typecheck:test 修存量后，是否进 preflight 门禁（start 前阻断）
- worktree build 失败是否应作为 `block --reason infra` 的标准场景
