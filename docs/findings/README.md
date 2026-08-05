# 发现总账

已被验证的技术发现与踩坑记录，跨 task 复用。一条目一文件，文件名 `dNNN_{slug}.md`。

- spike 结论收尾时抽一条到这里；spike 报告全文留在 `docs/archive/spikes/`。
- 日常发现（工具行为、平台差异、依赖坑、性能特征、被证伪的假设）随时追加。
- 只记**已验证**的事实与证据来源；推测和待确认的写进对应 task 的 spec 上下文区：只有用户或外部环境能核实的标 `UNVERIFIED-BLOCKING`，agent 可实验核实的标 `UNVERIFIED-SPIKE`。
- 发现是长期资产，不存在「闭环」，因此不迁 archive。失效时保留条目，改写 `- 现状` 为「YYYY-MM-DD 失效：原因」。
- `dNNN` 全局递增不复用。

## 命令

```bash
python3 scripts/repo_template/findings.py new --slug uv_lock_platform_marker
python3 scripts/repo_template/findings.py list
```

`new` 在 git 公共目录的排他锁内完成「扫描取号 → 建文件」，并发 worker 不会撞号；禁止手工创建条目文件。

## 字段

`- 来源` / `- 结论` / `- 证据` / `- 影响` / `- 现状`。`- 来源` 写 `sNNN` spike、`tNNN` task、或「日常」。新条目现状写「有效」。
