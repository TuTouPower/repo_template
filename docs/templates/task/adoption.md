# Adoption TNNN

owner 读 `review.md` 后逐条处置。决策自主，不经用户审阅，随 task commit 入库。

| finding_id | decision | rationale | status |
|------------|----------|-----------|--------|
| TNNN_code_f001 | 采纳 / 不采纳 | {一句话理由} | 已修 / 遗留-原因 / 无需修改 |
| TNNN_test_f001 | 采纳 / 不采纳 | {一句话理由} | 已修 / 遗留-原因 / 无需修改 |

字段说明：

- `decision`：采纳 / 不采纳。
- `rationale`：一句话理由。
- `status`：
    - `已修`：在本 task commit 内修复。
    - `遗留-原因`：未在本 commit 修复，原因写在 `-原因` 后。
    - `无需修改`：不采纳项专用。

处置路径：采纳且能当场修的立即修复（触代码或测试回单 task 流程 step 4 黑盒；仅文档改动区分笔误/事实，事实类触发局部重审）；不采纳的 `status` 标 `无需修改`；不能当场修的 `status` 标 `遗留-原因`，在 `task_report.md` 遗留问题中体现。

续写规则：首次复制本模板写入；后续处置在文件末尾追加 `## Round N (YYYY-MM-DD HH:MM)` 小节，对应本轮 review 的 finding；同 finding 在不同轮次决策变化各占一行，保留历史。
