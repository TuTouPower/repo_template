#!/usr/bin/env bash
# 读 task 目录下 review 报告，输出两路 verdict 与当前轮次（供 step 6 处置用）。
#
# 用法：
#   scripts/check_review_status.sh --task-dir docs/tasks/t001_foo
#
# 输出（stdout，一行键值，便于脚本/人读）：
#   code_verdict=PASS|FAIL|MISSING
#   test_verdict=PASS|FAIL|MISSING
#   overall=PASS|FAIL|INCOMPLETE
#   round=N               # 取两份报告 front 字段 round 的最大值；缺省时按 ## Round 小节推断，至少 1
#   max_review_round=N    # 当前双审上限（默认 2；blocked 后用户加轮则由调用方传入新值）

set -euo pipefail

task_dir=""
max_review_round=2

usage() {
    sed -n '2,12p' "$0" | sed 's/^# \?//'
    exit 1
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --task-dir) task_dir="${2:-}"; shift 2 ;;
        --max-review-round) max_review_round="${2:-}"; shift 2 ;;
        -h|--help) usage ;;
        *) echo "unknown arg: $1" >&2; usage ;;
    esac
done

[[ -n "$task_dir" ]] || usage

repo_root="$(git rev-parse --show-toplevel 2>/dev/null || true)"
if [[ -z "$repo_root" ]]; then
    repo_root="$(cd "$(dirname "$0")/.." && pwd)"
fi
if [[ "$task_dir" != /* ]]; then
    task_dir="$repo_root/$task_dir"
fi

code_f="$task_dir/review_code.md"
test_f="$task_dir/review_test.md"

extract_verdict() {
    local f="$1"
    if [[ ! -f "$f" ]]; then
        echo "MISSING"
        return
    fi
    # 取文件中最后一次 verdict 行（Round 追加后以最后一轮为准）
    local line
    line="$(grep -E '^verdict:[[:space:]]*(PASS|FAIL)\b' "$f" | tail -1 || true)"
    if [[ -z "$line" ]]; then
        echo "MISSING"
        return
    fi
    if [[ "$line" =~ PASS ]]; then
        echo "PASS"
    else
        echo "FAIL"
    fi
}

extract_round() {
    local f="$1"
    if [[ ! -f "$f" ]]; then
        echo 0
        return
    fi
    local r
    r="$(grep -E '^-[[:space:]]*round:[[:space:]]*[0-9]+' "$f" | tail -1 | grep -oE '[0-9]+$' || true)"
    if [[ -n "$r" ]]; then
        echo "$r"
        return
    fi
    # 数 ## Round N 标题
    local n
    n="$(grep -cE '^##[[:space:]]+Round[[:space:]]+[0-9]+' "$f" 2>/dev/null || true)"
    if [[ "${n:-0}" -gt 0 ]]; then
        echo "$n"
        return
    fi
    # 有报告正文则至少 round 1
    if grep -qE '^verdict:' "$f" 2>/dev/null; then
        echo 1
    else
        echo 0
    fi
}

code_verdict="$(extract_verdict "$code_f")"
test_verdict="$(extract_verdict "$test_f")"
r_code="$(extract_round "$code_f")"
r_test="$(extract_round "$test_f")"
round="$r_code"
if [[ "$r_test" -gt "$round" ]]; then
    round="$r_test"
fi
if [[ "$round" -lt 1 ]]; then
    round=1
fi

overall="INCOMPLETE"
if [[ "$code_verdict" == "MISSING" || "$test_verdict" == "MISSING" ]]; then
    overall="INCOMPLETE"
elif [[ "$code_verdict" == "PASS" && "$test_verdict" == "PASS" ]]; then
    overall="PASS"
else
    overall="FAIL"
fi

printf 'code_verdict=%s\n' "$code_verdict"
printf 'test_verdict=%s\n' "$test_verdict"
printf 'overall=%s\n' "$overall"
printf 'round=%s\n' "$round"
printf 'max_review_round=%s\n' "$max_review_round"
