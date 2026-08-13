# 工作流实战复盘

> 落地状态见 `decision_log.md`。§1 方案 C（不切分支、全在 main 上做）未被采纳为全局教条——它与 `workflow_reflection_2.md`（原 `incident_t071_worktree_loss.md`）回答的不是同一问题，当前规则是默认 worktree 隔离（L4/L30）。

基于 omni_media 项目执行 21 个 task（t041-t061）的完整记录复盘。与 `workflow_reflection_1.md`（原 `workflow_feedback.md`，理论分析）互补，本文聚焦**实战中暴露的、之前未预测到的问题**。

## 执行概况

|指标|数据|
|---|---|
|task 总数|21（20 done + 1 dropped 后拆为 6 个子 task 全 done）|
|审阅 task|10 个走了完整审阅（t041-t046）；其余 11 个直接收尾|
|审阅轮次|t041: 3 轮, t042: 4 轮, t043: 4 轮, t044: 5 轮, t045: 4 轮, t046: 3 轮|
|总 commit|31|
|测试基线→最终|343 → 370 passed（+27 用例）|
|分支数|11 个独立分支 + 4 个在 main 上直接做|

## 实战暴露的结构性问题

### 1. 多分支 merge 冲突是灾难（最大问题）

**现象**：11 个分支从同一 main base 切出，每个分支的 task.py finish 都改了 `tasks_index.json` + `archive/tasks_index.json`。merge 时这两个 JSON 文件**必然冲突**。用 `-X theirs` 策略批量合并后，main 上已 finish 的状态被分支的旧版本（backlog）覆盖，导致：

- tasks_index.json 显示已完成 task 仍为 backlog
- archive/tasks_index.json 缺失大量归档条目
- docs/tasks/ 里残留已归档的旧目录

修复花了 3 次 commit 才理清。

**根因**：task.py 把状态存在 JSON 文件里，每个分支独立修改，git 无法智能合并 JSON 内容变更。

**改进方向**：

- **方案 A（推荐）**：tasks_index.json 只在 main 上维护，分支不修改。task.py finish 改为在 merge 后统一执行（类似 `git rebase --autosquash`）。
- **方案 B**：task.py finish 时把状态写入 task.md front matter（每个文件独立，不冲突），tasks_index.json 由脚本扫描 task.md 生成（derived data）。
- **方案 C**：不切分支，所有 task 直接在 main 上做（t048/t049/t051 验证了这种方式无冲突）。

### 2. 审阅对低风险 task 是纯浪费

**现象**：21 个 task 中 11 个直接收尾（跳过审阅），包括：

- 纯文档 task（t052-t054）
- 纯配置 task（t055-t056）
- 纯格式 task（t057-t061）

这些 task 改动无业务逻辑，reviewer 能审出的 finding 极少（多为"文档元引用"等 minor），但流程仍要求派 2 个 sub agent。

**改进方向**：按 task 类型分级：

- **关键代码**（安全/资金/并发/鉴权）：完整审阅 + e2e
- **普通代码**（API/前端/测试）：单审 + 单测
- **文档/配置/格式**：build + test 通过即可，无 reviewer

### 3. max_review_round=2 对复杂 task 不够

**现象**：t044（计费幂等）用了 5 轮审阅才收敛。前几轮 reviewer 发现大量测试缺口（mock 边界过宽、AC 未覆盖），每轮补测试后又暴露新路径。用户中途把 max 从 2 改到 5。

**根因**：spec 写了 6 项 AC，但测试覆盖是渐进式的——第一轮发现缺口→补→第二轮发现新缺口→补。这种"剥洋葱"式收敛在复杂 task 上无法在 2 轮内完成。

**改进方向**：

- 复杂 task（spec 验收标准 ≥ 5 条或跨 ≥ 3 个文件）默认 max_review_round=5
- 简单 task（单文件或纯文档）max_review_round=1
- 在 spec front matter 加 `complexity: high|medium|low` 字段，task.py 根据它推荐 max

### 4. plan.md 在实战中几乎没用

**现象**：21 个 task 的 plan.md 从未被实施时参考。agent 实施时直接读 spec（验收标准）+ 代码现状。plan 里写的"步骤与验证"和 spec 的"验收标准"高度重复。

**改进方向**：

- 简单 task 不要求 plan.md（spec 验收标准已足够）
- 复杂 task 的 plan.md 改为"实施设计笔记"（文件级改动清单 + 关键技术决策 + 踩坑预判），不复述 spec

### 5. 批量缩进转换破坏测试

**现象**：t057-t061 用 Python 脚本批量把 4 空格缩进改 2 空格，导致 t033_proxy_pool 测试失败——脚本的 `indent // 4 * 2` 算法对非 4 倍数的缩进行（如对齐空格）处理不正确，破坏了测试内的字符串匹配。

**根因**：缩进转换不是简单的"行首空格除以 2"，需要理解语义（缩进 vs 对齐）。ESLint/Prettier 能正确处理，手写脚本不能。

**改进方向**：

- 缩进统一用 `npx eslint --fix` 或 `npx prettier --write`（确保规则配对）
- 不用手写 Python 脚本做缩进转换

### 6. spec 验收标准与环境依赖脱节

**现象**：多个 task 的验收标准写了"smoke 通过"或"e2e 不退化"，但 agent 在 WSL 环境无法运行 smoke/e2e（需要 DB + Auth + Storage + server）。这些 AC 实际无法在开发时验证。

**改进方向**：

- spec 验收标准区分"agent 可验证"（npm test / npm run build）与"部署侧验证"（smoke / e2e）
- 后者标为 `[deploy]` 前缀，agent 实施时跳过，部署时执行

### 7. "一个 task 一个 commit"与实际脱节

**现象**：

- t041 内含 6 个独立修复点，最终一个 commit 打包
- t048+t049+t051 合并成一个 commit
- t053+t054 合并成一个 commit

实际执行中 commit 粒度由"方便 merge"驱动，不是由"一个 task 一个 commit"规则驱动。

**改进方向**：把"一个 task 一个 commit"改为"一个 task 一个主题，N 个 commit"：

- task = 一组逻辑相关改动
- commit = task 内的原子提交
- merge 到 main 时保留 commit 历史

## 做得好的设计（保留）

1. **specs driven 防漂移**：spec 在前确实让"做什么"先于"怎么做"清晰
2. **max_review_round 上限**：防止无限循环（即使 5 轮也是有限的）
3. **blocked 机制**：t050 dropped → 拆分为 t056-t061 是正确的终止决策
4. **task.py 自动化**：降低状态管理心智负担（虽然有 merge 冲突问题）
5. **adoption_decision.md**：16 份 review 报告 → 63 采纳项 → 15 个 task 的完整追溯链非常有效
6. **审阅对关键 task 的价值**：t041/t044/t045 的 reviewer 发现了真实 critical bug（new Function RCE、余额泄漏、幂等缺口）

## 改进建议（按优先级）

### P0：解决 merge 冲突（最大痛点）

**推荐方案 C**：不切分支，所有 task 直接在 main 上做。

理由：

- t048/t049/t051/t056-t061 共 10 个 task 在 main 上直接做，零冲突
- 11 个分支 merge 时大量冲突 + 3 次修复 commit
- 分支的唯一好处是"隔离实验"，但 task.py 已提供 status 管理（backlog/active/blocked/done）
- main 上的 commit 历史本身就是 task 的审计轨迹

如果保留分支：改用方案 B（task.py 把状态写入 task.md front matter，tasks_index.json 改为 derived data，merge 时不冲突）。

### P1：审阅分级

在 task spec front matter 加 `review_level: full|single|none`：

- `full`：审阅 + e2e（安全/资金/并发/鉴权）
- `single`：单审 + 单测（普通 API/前端/测试）
- `none`：build + test（文档/配置/格式）

agent 根据 review_level 决定是否派 reviewer。

### P2：plan.md 按复杂度可选

- `complexity: low`（单文件或纯文档）：不要求 plan.md
- `complexity: medium`（2-5 文件）：plan.md 写关键决策 + 文件清单
- `complexity: high`（>5 文件或跨模块）：plan.md 写完整实施设计

### P3：工具替代手写脚本

- 缩进统一：`npx eslint --fix` 或 `npx prettier --write`
- 文件重命名 + import 更新：IDE refactoring 或 `jscodeshift`
- 不用手写 Python/sed 脚本做代码变换

## 一句话总结

**工作流在"防止 agent 犯错"方面做得好（审阅/门禁/blocked），但在"agent 的执行效率"方面有结构性瓶颈（merge 冲突/审阅无差别/plan 退化）。最大的改进杠杆是：去掉 task 分支（直接在 main 做）+ 审阅按风险分级。**
