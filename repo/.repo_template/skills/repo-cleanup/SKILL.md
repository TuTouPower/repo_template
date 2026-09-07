---
name: repo-cleanup
description: none
disable-model-invocation: true
---

# repo-cleanup

清理缓存、构建产物和无用运行文件。默认 dry-run；脚本参数和分类以 `repo_cleanup.py --help` 为准。

## 流程

1. 在消费仓主仓根运行 `repo_cleanup.py scan`，报告分类、路径和被 keep 保护的内容；脚本不报告大小，需要时另行只读统计。
2. 用户未指定范围时采用保守类别；不把源码、测试、配置、文档、数据或未知目录当垃圾。
3. 删除前将 scan 清单与 `git ls-files` 交叉核对。脚本按类别扫描，不以是否 tracked 自动保护；含受保护内容、仍在使用的文件或无法恢复的数据时停止。保存必须保留的未跟踪内容。须保留的路径用 `--keep`（精确 / glob / 前缀均生效，文件级命中同样排除）；scan 清单中仍出现应保留路径时停止，不执行 apply。
4. 用户确认后用相同类别和 keep 参数执行 apply；apply 会重新扫描而非消费旧清单，期间文件变化需重新确认。失败时停止，检查已删范围，从备份恢复需要保留的内容，不盲目重试。
5. 清理后运行 `git status --short`，确保没有误删 tracked 文件。

## 边界

- 不使用通配 `rm` 代替脚本保护。
- 不清 `.git`、`.repo_template`、docs 账本、`.env`、用户数据或未识别目录。
- 用户给出的 keep 始终优先。
