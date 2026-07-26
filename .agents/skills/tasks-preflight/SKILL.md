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

2. **逐 task 查缺口**。读 `docs/tasks/{tid}_{slug}/` 的 `spec.md`（依赖与约束、AC）、`plan.md`（步骤、风险）、`task.md`（过程记录、阻塞说明）。对照 `.env.example`（若有）与 spec/plan 点名的环境变量，列出指向密钥或外部服务的 key；本地是否已配置只查存在性（如 `grep -q '^KEY=' .env`），不读取值。

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

3. **输出缺口表**：

   ```markdown
   ## Preflight 结果

   范围：<实际查了什么>

   | tid | 标题 | 状态 | 缺口 | 阻塞? | 请用户做什么 |
   |-----|------|------|------|-------|--------------|
   | t002 | … | active | 缺 OPENAI_API_KEY | 是 | 写入本地 .env（勿提交） |
   | t003 | … | backlog | 无 | — | — |

   跳过：
   - t009：status=done，非待做

   结论：
   - 有阻塞：先补齐「是」的行，再 /tasks-run
   - 或：无阻塞；执行请 /tasks-run（参数可与本次一致）
   ```

## 边界

- 只读：不改代码、测试、JSON、环境。
- 不自动执行 `tasks-run`；无阻塞时只**提示**用户可自行 `/tasks-run`。

## 完成

输出缺口表 + 结论。有阻塞→先补齐再 run；无→提示 `/tasks-run`。
