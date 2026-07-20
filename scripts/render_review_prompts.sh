#!/usr/bin/env bash
# 从 task.md front matter 渲染 code/test reviewer 完整 prompt。
#
# 用法：
#   scripts/render_review_prompts.sh --task-dir docs/tasks/T001_my_slug
#   scripts/render_review_prompts.sh --task docs/tasks/T001_my_slug/task.md
#   scripts/render_review_prompts.sh --task-dir ... --out-dir .scratch/review_prompts
#
# 必填 front matter：tid, slug, diff_anchor
# 可选：spec_path（默认 <task_dir>/spec.md）
# 默认 stdout；--out-dir 时写入 code_review_prompt.md 与 test_review_prompt.md

set -euo pipefail

task_path=""
task_dir=""
out_dir=""

usage() {
    sed -n '2,12p' "$0" | sed 's/^# \?//'
    exit 1
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --task) task_path="${2:-}"; shift 2 ;;
        --task-dir) task_dir="${2:-}"; shift 2 ;;
        --out-dir) out_dir="${2:-}"; shift 2 ;;
        -h|--help) usage ;;
        *) echo "unknown arg: $1" >&2; usage ;;
    esac
done

repo_root="$(git rev-parse --show-toplevel 2>/dev/null || true)"
if [[ -z "$repo_root" ]]; then
    repo_root="$(cd "$(dirname "$0")/.." && pwd)"
fi

if [[ -n "$task_path" && -n "$task_dir" ]]; then
    echo "use only one of --task or --task-dir" >&2
    exit 1
fi

if [[ -n "$task_dir" ]]; then
    if [[ "$task_dir" != /* ]]; then
        task_dir="$repo_root/$task_dir"
    fi
    task_path="$task_dir/task.md"
elif [[ -n "$task_path" ]]; then
    if [[ "$task_path" != /* ]]; then
        task_path="$repo_root/$task_path"
    fi
    task_dir="$(cd "$(dirname "$task_path")" && pwd)"
else
    echo "need --task-dir or --task" >&2
    usage
fi

if [[ ! -f "$task_path" ]]; then
    echo "missing task file: $task_path" >&2
    exit 1
fi

# 解析 YAML front matter（仅支持简单 key: value 行，值可带双引号）
fm_tid=""
fm_slug=""
fm_diff_anchor=""
fm_spec_path=""
in_fm=0
while IFS= read -r line || [[ -n "$line" ]]; do
    if [[ $in_fm -eq 0 ]]; then
        if [[ "$line" == "---" ]]; then
            in_fm=1
            continue
        fi
        echo "task.md must start with YAML front matter (---)" >&2
        exit 1
    fi
    if [[ "$line" == "---" ]]; then
        break
    fi
    # skip comments / empty
    [[ -z "${line// /}" || "$line" =~ ^# ]] && continue
    key="${line%%:*}"
    key="$(echo "$key" | sed 's/[[:space:]]//g')"
    val="${line#*:}"
    val="${val#"${val%%[![:space:]]*}"}"
    val="${val%"${val##*[![:space:]]}"}"
    val="${val#\"}"
    val="${val%\"}"
    val="${val#\'}"
    val="${val%\'}"
    case "$key" in
        tid) fm_tid="$val" ;;
        slug) fm_slug="$val" ;;
        diff_anchor) fm_diff_anchor="$val" ;;
        spec_path) fm_spec_path="$val" ;;
    esac
done <"$task_path"

if [[ -z "$fm_tid" || -z "$fm_slug" || -z "$fm_diff_anchor" ]]; then
    echo "front matter requires tid, slug, diff_anchor (got tid='$fm_tid' slug='$fm_slug' diff_anchor='$fm_diff_anchor')" >&2
    exit 1
fi

# tid 必须是大写任务 ID（T001），禁止 t001
if [[ ! "$fm_tid" =~ ^T[0-9]+$ ]]; then
    echo "tid must be uppercase task id like T001 (got '$fm_tid')" >&2
    exit 1
fi

# task_dir relative to repo for placeholders when under repo
rel_task_dir="$task_dir"
case "$task_dir" in
    "$repo_root"/*) rel_task_dir="${task_dir#"$repo_root"/}" ;;
esac

if [[ -n "$fm_spec_path" ]]; then
    spec_path="$fm_spec_path"
else
    spec_path="$rel_task_dir/spec.md"
fi

tid="$fm_tid"
slug="$fm_slug"
diff_anchor="$fm_diff_anchor"
task_dir_ph="$rel_task_dir"

prompts_dir="$repo_root/docs/templates/prompts"
code_src="$prompts_dir/code_review_prompt.md"
test_src="$prompts_dir/test_review_prompt.md"
share_src="$prompts_dir/share_review_prompt.md"

for f in "$code_src" "$test_src" "$share_src"; do
    if [[ ! -f "$f" ]]; then
        echo "missing template: $f" >&2
        exit 1
    fi
done

apply_placeholders() {
    sed \
        -e "s|{TID}|${tid}|g" \
        -e "s|{slug}|${slug}|g" \
        -e "s|{spec_path}|${spec_path}|g" \
        -e "s|{task_dir}|${task_dir_ph}|g" \
        -e "s|{diff_anchor}|${diff_anchor}|g"
}

render_one() {
    local axis_src="$1"
    {
        cat "$axis_src"
        echo
        cat "$share_src"
    } | apply_placeholders
}

if [[ -n "$out_dir" ]]; then
    if [[ "$out_dir" != /* ]]; then
        out_dir="$repo_root/$out_dir"
    fi
    mkdir -p "$out_dir"
    render_one "$code_src" >"$out_dir/code_review_prompt.md"
    render_one "$test_src" >"$out_dir/test_review_prompt.md"
    echo "wrote $out_dir/code_review_prompt.md" >&2
    echo "wrote $out_dir/test_review_prompt.md" >&2
else
    echo "===== code_review_prompt ====="
    render_one "$code_src"
    echo
    echo "===== test_review_prompt ====="
    render_one "$test_src"
fi
