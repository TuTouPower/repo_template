---
name: repo-hygiene
description: 归档已确认过时的 review 会话产物至 docs/archive/reviews/，先 dry-run。用户要求归档过时评审或整理 review 记录时调用。
disable-model-invocation: true
metadata:
  opencode/autoinvoke: "false"
---

# repo-hygiene

归档已确认过时的 review 会话产物至 `docs/archive/reviews/`；涉及迁移和删除时先 dry-run，拿不准就报告用户。

## 组合契约

仅用户显式授权入口或 registry.json 已登记流程可执行；作为子流程时由父流程直接读取本正文，原样传递参数、identity、owner_id 与授权，不扩大授权。完整规则见 `.repo_template/docs/usage.md`「skill 调用」。

## 处理对象

仅处理用户确认过时的多路 review 会话目录（`docs/reviews/review_*/`）：
- 整包整目录迁移至 `docs/archive/reviews/`。
- 保留报告 `review_*.md` 与 `_meta/` 过程文件。
- 执行命令：`python3 .repo_template/scripts/repo_hygiene.py archive-review <dir> [--write]`。默认 dry-run，加 `--write` 才落盘。
- 目标路径已存在时拒绝覆盖或合并。

## 保护

- 仅限独立冷文稿归档，不扩展为通用路径搬运器。
- 不得操作 review 以外任何路径：禁止操作 task、pending、spike、业务源码、`.repo_template/docs/task_template/`、review prompt、spike template、skill/AGENTS 正文和 `docs_repo/`。
- `docs/archive/reviews/` 保持追加历史，不覆盖已存在的同名归档目标。

## 完成

报告迁入 archive、保留、跳过和仍需用户决定的项目；按用户要求提交一个 hygiene commit 或保留工作区。
