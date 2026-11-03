"""E1：plan --serial 按队列内关系分链，给出每个成员的 chain 与 base（real-git）。"""

import json

from test_dispatch_integration import git_repo, _git, _task_cli
from repo_task.documents import parse_front_matter, write_front_matter


def _relate(repo, tid, **values):
    """改 front matter 关系字段并提交，使 start 从含关系的 HEAD 创建 worktree。"""
    path = next((repo / "docs/tasks").glob(f"{tid}_*/note.md"))
    frontmatter, body = parse_front_matter(path)
    frontmatter.update(values)
    write_front_matter(path, frontmatter, body)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", f"graph: {tid}")


def _plan_json(repo, *tids):
    result = _task_cli(repo, "plan", "--serial", "--json", *tids)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def _rows(plan):
    return {
        row["taskId"]: (row["chain"], row["base"]["tid"], row["base"]["branch"])
        for row in plan["members"]
    }


# --------------------------------------------------------------------------
# E1：plan --serial 的 base / chain
# --------------------------------------------------------------------------


def test_plan_serial_json_gives_chain_and_base_and_keeps_existing_fields(git_repo):
    # t003 单向声明冲突 t001；t002 与二者无关
    _relate(git_repo, "t003", conflicts_with="t001")
    plan = _plan_json(git_repo)

    assert plan["mode"] == "serial"
    assert [chain["taskIds"] for chain in plan["chains"]] == [["t001", "t002", "t003"]]
    assert {"deferred", "unassigned", "crossings", "unlocks", "titles", "summary", "project"} <= set(plan)
    assert _rows(plan) == {
        "t001": (1, None, "main"),
        "t002": (2, None, "main"),
        "t003": (1, "t001", "t001_alpha"),
    }
    assert plan["queue_chains"] == [
        {"chain": 1, "taskIds": ["t001", "t003"], "tail": "t003"},
        {"chain": 2, "taskIds": ["t002"], "tail": "t002"},
    ]


def test_plan_serial_depends_on_and_two_earlier_relations_form_one_chain(git_repo):
    # t003 依赖 t001、与 t002 冲突：t001/t002 互无关系，三者仍归一条链
    _relate(git_repo, "t003", depends_on="t001", conflicts_with="t002")
    plan = _plan_json(git_repo)

    assert _rows(plan) == {
        "t001": (1, None, "main"),
        "t002": (1, "t001", "t001_alpha"),
        "t003": (1, "t002", "t002_beta"),
    }
    assert [chain["tail"] for chain in plan["queue_chains"]] == ["t003"]


def test_plan_serial_explicit_queue_keeps_order_and_recomputes_groups(git_repo):
    _relate(git_repo, "t002", conflicts_with="t001,t003")
    # 队列外 t002 不连边：只剩 t001/t003 无关，各自从主干起步
    plan = _plan_json(git_repo, "t003", "t001")
    assert [chain["taskIds"] for chain in plan["chains"]] == [["t003", "t001"]]
    assert _rows(plan) == {
        "t003": (1, None, "main"),
        "t001": (2, None, "main"),
    }
    # 含 t002 时三者经 t002 归一条链，base 按给定顺序紧邻前一成员
    plan = _plan_json(git_repo, "t003", "t002", "t001")
    assert _rows(plan) == {
        "t003": (1, None, "main"),
        "t002": (1, "t003", "t003_gamma"),
        "t001": (1, "t002", "t002_beta"),
    }


def test_plan_serial_explicit_queue_rejects_bad_queues(git_repo):
    _relate(git_repo, "t002", depends_on="t001")
    wrong_order = _task_cli(git_repo, "plan", "--serial", "t002", "t001")
    assert wrong_order.returncode != 0
    assert "invalid_queue" in wrong_order.stderr
    assert "t001" in wrong_order.stderr and "t002" in wrong_order.stderr

    unknown = _task_cli(git_repo, "plan", "--serial", "t001", "t009")
    assert unknown.returncode != 0 and "t009" in unknown.stderr

    duplicate = _task_cli(git_repo, "plan", "--serial", "t001", "t001")
    assert duplicate.returncode != 0 and "重复" in duplicate.stderr

    without_serial = _task_cli(git_repo, "plan", "t001")
    assert without_serial.returncode != 0 and "--serial" in without_serial.stderr


def test_plan_serial_text_lists_members_and_merge_commands(git_repo):
    _relate(git_repo, "t002", depends_on="t001")
    result = _task_cli(git_repo, "plan", "--serial")
    assert result.returncode == 0, result.stderr
    assert "[链1 base=main]" in result.stdout
    assert "[链1 base=t001_alpha]" in result.stdout
    assert "[链2 base=main]" in result.stdout
    assert "integrate-chain t002" in result.stdout
    assert "integrate t003" in result.stdout


def test_plan_batch_json_does_not_gain_queue_fields(git_repo):
    result = _task_cli(git_repo, "plan", "--json")
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert "members" not in payload and "queue_chains" not in payload
