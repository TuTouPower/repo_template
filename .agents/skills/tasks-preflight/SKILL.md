---
name: tasks-preflight
description: none
disable-model-invocation: true
---

# tasks-preflight

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

1. **列待做 task**。CLI 一次只带一个 `--status`，多状态多次 list 合并去重、按 tid 升序：

   ```bash
   scripts/task.py list --status backlog
   scripts/task.py list --status active
   scripts/task.py list --status blocked
   scripts/task.py show t002    # 点名 tid 时
   ```

   输出 `(no tasks)` 是正常空态。清单空且无跳过：回复「当前没有待做 task 需要 preflight」，结束。

2. **跑机器门禁**（每个 task 各一次，只读）：

   ```bash
   scripts/task.py preflight {tid}
   ```

   它查状态、spec 完整、工作区一致性与未知契约分类。`UNVERIFIED-BLOCKING`、裸 `UNVERIFIED` 和其它 FAIL 项直接进输出表，标「阻塞」；`UNVERIFIED-SPIKE` 只警告，属于执行期 Step 1 工作。

3. **逐 task 查用户侧缺口**。读 `docs/tasks/{tid}_{slug}/` 的 `spec.md`（契约区 AC、上下文区依赖与约束、未知契约清单）、`task.md`（实施笔记、阻塞说明）。对照 `.env.example`（若有）与 spec 点名的环境变量，列出指向密钥或外部服务的 key；本地是否已配置只查存在性（如 `grep -q '^KEY=' .env`），不读取值。

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

   每条标严重度：**阻塞**（缺它 `/tasks-run` 跑不下去）/ **可后补**（能开干但某条 AC 或上线会缺）。

4. **输出缺口表**：

   ```markdown
   ## Preflight 结果

   范围：<实际查了什么>

   | tid | 标题 | 状态 | preflight | 缺口 | 阻塞? | 请用户做什么 |
   |-----|------|------|-----------|------|-------|--------------|
   | t002 | … | active | PASS | 缺 OPENAI_API_KEY | 是 | 写入本地 .env（勿提交） |
   | t003 | … | backlog | FAIL：spec 缺契约区 | 无 | 是 | 补全 spec 契约区 |

   跳过：
   - t009：status=done，非待做

   结论：
   - 有阻塞：先补齐「是」的行，再 /tasks-run
   - 或：无阻塞；执行请 /tasks-run（参数可与本次一致）
   ```

## 边界

- 只读：不改代码、测试、task 状态、环境。`task.py preflight` 只读不写。
- 不自动执行 `tasks-run`；无阻塞时只**提示**用户可自行 `/tasks-run`。

## 完成

输出缺口表 + 结论。有阻塞→先补齐再 run；无→提示 `/tasks-run`。
