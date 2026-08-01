---
name: task-preflight
description: none
disable-model-invocation: true
---

# task-preflight

只读，不修改任何文件。查待做 task 还需用户提供什么。

## 输入

| 用户输入 | 检查范围 |
|----------|----------|
| 无参数 | `backlog` ∪ `active` ∪ `blocked` 全部 |
| 状态词（`backlog` / `active` / `blocked`，可组合） | 这些状态的全部 task |
| tid（`tNNN`，可多个） | 这些 tid 中状态属于上述三态的；非待做记「跳过」 |
| 状态词 + tid | 两者并集 |

`done` / `dropped` 永不在范围。

## 步骤

1. **建立状态视图**。main 在链式批次期间可能仍显示旧 backlog；按以下优先级确定每个 tid 的有效状态与读取位置：

   1. 登记 worktree：进入该 worktree，`scripts/task.py show {tid}`。
   2. 未合并 task 分支链：列出分支并按 Git ancestry 找各链尾，在链尾执行 `scripts/task.py show/list --ref {branch}`。
   3. main：只代表尚未进入链的 task 与已合并归档状态。

   ```bash
   scripts/task.py list --status backlog
   git worktree list --porcelain
   git branch --no-merged <default> --list 't[0-9]*_*'
   scripts/task.py list --ref {chain_tail}
   ```

   多条链中同一 tid 出现冲突状态时列为阻塞性状态冲突，不猜。按输入范围合并去重、tid 升序；effective status 为 `done` / `dropped` 的记「跳过」。清单空且无跳过：回复「当前没有待做 task 需要 preflight」，结束。

2. **跑机器门禁**（每个 task 各一次，只读，按有效状态与来源选择位置）：

   | 有效状态 / 来源 | 命令 |
   |-----------------|------|
   | main 中尚未进入链的 `backlog` | `scripts/task.py preflight {tid} --allow-backlog` |
   | 链尾 ref 中等待后续 start 的 `backlog` | `scripts/task.py preflight {tid} --allow-backlog --ref {chain_tail}` |
   | 登记 worktree 中的 `active` | 在该 worktree 执行 `scripts/task.py preflight {tid}` |
   | 登记 worktree 中的 `blocked` | 在该 worktree 执行 `scripts/task.py preflight {tid}`，保留 blocked FAIL |

   `--ref` 只检查快照状态、spec 与 front matter，不检查 worktree 和当前脏改动；输出该警告属预期，不算用户缺口。机器门禁检查状态、spec 完整、工作区一致性与未知契约分类。`UNVERIFIED-BLOCKING`、裸 `UNVERIFIED` 和其它 FAIL 项直接进输出表，标「阻塞」；`UNVERIFIED-SPIKE` 只警告，属于执行期 Step 1 工作。

3. **逐 task 查用户侧缺口**。从上一步确定的有效来源读取 `spec.md` 与 `task.md`：worktree 直接读文件，链中 backlog 从链尾 ref 读取，main backlog 从主仓读取。检查契约区 AC、上下文区依赖与约束、未知契约清单、实施笔记与阻塞说明。对照 `.env.example`（若有）与 spec 点名的环境变量，列出指向密钥或外部服务的 key；本地是否已配置只查存在性（如 `grep -q '^KEY=' .env`），不读取值。

   未知契约按 spec 标记处理：
   - `UNVERIFIED-BLOCKING`：只有用户或外部环境能核实，属于阻塞缺口；核实并改写结论前不得 `start`。
   - `UNVERIFIED-SPIKE`：agent 可自行实验，不算用户侧缺口；执行期 Step 1 完成实验后须删除标记并改写结论。
   - 裸 `UNVERIFIED`：分类不明，属于阻塞性 spec 格式错误。

   只记**必须用户提供、agent 不能编造**的缺口：

   | 类型 | 举例 |
   |------|------|
   | 密钥 | API token、DB 密码 |
   | 环境 | 需启动的服务、端口、平台限制 |
   | 账号权限 | 云控制台、第三方组织 |
   | 产品决策 | 方案 A/B、范围取舍、blocked 后加轮或 drop |
   | 外部数据 | 样例文件、回调 URL |

   不算缺口：读代码/文档能搞定的；agent 可装可查且不违硬约束的。

   每条标严重度：**阻塞**（缺它 `/task-run` 跑不下去）/ **可后补**（能开干但某条 AC 或上线会缺）。

4. **输出缺口表**：

   ```markdown
   ## Preflight 结果

   范围：<实际查了什么>

   | tid | 标题 | 有效状态 | 来源 | preflight | 缺口 | 阻塞? | 请用户做什么 |
   |-----|------|----------|------|-----------|------|-------|--------------|
   | t002 | … | active | worktree `../repo_t002` | PASS | 缺 OPENAI_API_KEY | 是 | 写入本地 .env（勿提交） |
   | t003 | … | backlog | ref `t002_xxx` | FAIL：spec 缺契约区 | 无 | 是 | 补全 spec 契约区 |

   跳过：
   - t009：status=done，非待做

   结论：
   - 有阻塞：先补齐「是」的行，再 /task-run
   - 或：无阻塞；执行请 /task-run（参数可与本次一致）
   ```

## 边界

- 只读：不改代码、测试、task 状态、环境。`task.py preflight`、`list/show --ref` 只读不写。
- 不把 main 中被 worktree或未合并链覆盖的旧 backlog 当成待启动 task。
- 不自动执行 `task-run`；无阻塞时只**提示**用户可自行 `/task-run`。

## 完成

输出缺口表 + 结论。有阻塞→先补齐再 run；无→提示 `/task-run`。
