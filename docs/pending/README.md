# 待办与不办总账

项目里「已知、还欠着」的事只在本目录登记：未修 bug、review 遗留、技术债、该做未做的需求，以及用户已确认暂搁的事项。

持续口述登记用 skill `pending-record`（澄清后派子代理落盘；bug 走 `task-bug` 分析再记）。从总账捞成 task 用 `task-from-pending`。

一条目一文件，文件名 `pNNN_{slug}.md`；三态由所在目录表达：

|目录|语义|
|---|---|
|`docs/pending/todo/`|未闭环、待启动|
|`docs/pending/parked/`|用户显式确认暂搁（不办）；不等于闭环|
|`docs/archive/pending/`|已闭环|

`pNNN` 全局递增，跨三个目录共享一条序列，历史编号不复用。已验证的技术发现不属于待办，写 `docs/findings/`。

## 命令

```bash
python3 scripts/repo_template/pending.py new --slug cli_exit_code            # 建普通条目
python3 scripts/repo_template/pending.py new --slug crash_on_empty --kind bug # 建 bug 条目
python3 scripts/repo_template/pending.py list --state all                     # 列举
python3 scripts/repo_template/pending.py archive p047 --fix-ref t012 --write  # 闭环
python3 scripts/repo_template/pending.py park p047 --reason "等外部依赖" --write
python3 scripts/repo_template/pending.py revive p047 --write                  # parked → todo
```

`new` 在 git 公共目录的排他锁内完成「扫描取号 → 建文件」，并发 worker 不会撞号；禁止手工创建条目文件。迁移一律走命令，命令默认 dry-run，加 `--write` 落盘。

## 字段

两种模板，按条目性质选一种。`- 处理` 未闭环写「未开」，闭环写 `{tid}` 或外部动作说明，暂搁写「不办」。

- 普通（需求 / 遗留 / 技术债）：`- 来源` / `- 内容` / `- 处理`。`- 来源` 写清出处：finding_id、原 tid、用户提出，或技术债自查。
- bug：`- 现象` / `- 影响` / `- 根因` / `- 测试缺口` / `- 线索` / `- 处理`。bug 由 `task-bug` 或 `pending-record`（派子代理跑 task-bug 第 1–6 步：含同类位点扫描与合并）登记并完成根因与补测分析。`- 根因` 写共享机制与已确认同类位点清单（或「已扫无同类」）；`- 影响` / `- 测试缺口` 覆盖合并后的位点并集。

`parked/` 条目追加 `- 暂搁：YYYY-MM-DD 决定不办的理由`，写清为什么现在不动（风险可控、排期靠后、等外部依赖等）。复活时 `revive` 会删该字段并把 `- 处理` 改回「未开」，保留原编号。
