# Review 报告落点

reviewer 把报告写到本目录的 `review_code.md` / `review_test.md`（文件名固定，多轮追加不覆盖）。

**报告格式的唯一定义在 `docs/reviews/prompts/` 的 prompt 模板**，由 `scripts/render_review_prompts.py` 渲染到 `.scratch/review_prompts/`。本文件不复制格式骨架，避免两处定义漂移。

finding 的处置表写在 `task.md` 的 `## Review 处置`，不写进 review 报告。
