---
name: repo-cleanup
description: 清理仓库缓存、构建产物与运行垃圾（pycache/pytest/logs/OS/编辑器/scratch），默认 dry-run；scratch 默认清理，3 天内有主草稿保留；控制面锁被占用时带 scratch 的 cleanup 立即失败、零删除；node/artifacts/data 须点名。用户要求清理仓库垃圾、释放空间或核对可清文件时调用。
disable-model-invocation: true
metadata:
  opencode/autoinvoke: "false"
---

# repo-cleanup

清理缓存、构建产物和无用运行文件。默认 dry-run；脚本参数和分类以 `repo_cleanup.py --help` 为准。

## 组合契约

仅用户显式授权入口或 registry.json 已登记父流程可执行；作为子流程时由父流程直接读取本正文，原样传递参数、identity、owner_id 与授权，不扩大授权。完整规则见 `.repo_template/docs/usage.md`「skill 调用」。

## 流程

1. 在消费仓主仓根运行 `repo_cleanup.py scan`，报告分类、路径、保护中的草稿和被 keep 的内容；scratch 默认参与。事务数据不在 `.scratch/`、记录在 Git common 目录，`repo-cleanup` 不读取也不清理事务记录；旧 `.scratch/repo_sync_rollback` 的 applied/未完成或损坏事务必须用升级前消费仓脚本（旧版 `repo_sync.py`）执行 `transaction accept` / `transaction rollback` 处理（现场变化时 rollback 需加 `--confirm-restore`），规程见 `.repo_template/docs/sync.md`「同步准备与 apply 事务」段的旧版事务维护；当前 `transaction` 只读 Git common 目录，看不到旧 `.scratch` 事务，不自动接管或清理，也不能用当前命令 accept/rollback 旧记录；旧工具不可用时停止清理并人工核验恢复，不盲目重试、不把旧目录当垃圾删；带 scratch 的 cleanup 在控制面锁被占用时立即失败、零删除。脚本不报告大小，需要时另行只读统计。
2. 用户未指定范围时采用默认类别；不把源码、测试、配置、文档、数据或未知目录当垃圾。scratch 只按脚本判定：无主、超期草稿才进入候选，3 天内有主草稿保留。
3. 删除前将 scan 清单与 `git ls-files` 交叉核对。脚本按类别扫描，不以是否 tracked 自动保护；含受保护内容、仍在使用的文件或无法恢复的数据时停止。保存必须保留的未跟踪内容。须保留的路径用 `--keep`（精确 / glob / 前缀均生效，文件级命中同样排除）；scan 清单中仍出现应保留路径时停止，不执行 apply。
4. 用户确认后用相同类别和 keep 参数执行 apply（可传入 `--expected-count` 锁定 scan 清单数量，防止期间新增未确认文件被误删）；apply 会重新扫描，若期间候选发生变化（数量不符）将停止并要求重新确认。带 scratch 时脚本非阻塞持有控制面锁，锁被同步或 task 控制命令占用则整次立即失败、零删除，稍后重试；失败时停止，检查已删范围，从备份恢复需要保留的内容，不盲目重试。
5. 清理后运行 `git status --short`，确保没有误删 tracked 文件。

## 边界

- 不使用通配 `rm` 代替脚本保护。
- 不清 `.git`、`.repo_template`、docs 账本、`.env`、用户数据或未识别目录；仓库根 `local/` 是用户私有目录，脚本不扫描不删除，也不得手工清理。需要长期保留的文件放 `local/`，不要依赖 scratch 的 3 天保留期。
- 用户给出的 keep 始终优先。
