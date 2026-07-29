"""task.py 未知契约分类与门禁。"""

import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

from task import parse_unverified_contracts, unverified_contract_gate


def test_parser_only_reads_direct_items_in_unknown_contract_section():
    spec = """## 上下文区

说明文字提到 `UNVERIFIED-BLOCKING`，不属于条目。

### 未知契约清单

说明文字提到 `UNVERIFIED`，不属于条目。

```markdown
- 伪条目：UNVERIFIED-BLOCKING
```

- 用户账号：UNVERIFIED-BLOCKING，需用户核实
- 平台行为：UNVERIFIED-SPIKE，执行期实验
- 未分类行为：UNVERIFIED，待决定

### 风险与回退

- 风险：UNVERIFIED-BLOCKING 不应计入
"""

    assert parse_unverified_contracts(spec) == {
        "blocking": ["用户账号：UNVERIFIED-BLOCKING，需用户核实"],
        "spike": ["平台行为：UNVERIFIED-SPIKE，执行期实验"],
        "ambiguous": ["未分类行为：UNVERIFIED，待决定"],
    }


def test_default_gate_allows_spike_with_warning():
    spec = """### 未知契约清单

- 平台行为：UNVERIFIED-SPIKE，执行期实验
"""

    problems, warnings = unverified_contract_gate(spec)

    assert problems == []
    assert len(warnings) == 1
    assert "仅可执行 Step 1" in warnings[0]


def test_strict_gate_blocks_spike_and_other_unresolved_markers():
    spec = """### 未知契约清单

- 用户账号：UNVERIFIED-BLOCKING，需用户核实
- 平台行为：UNVERIFIED-SPIKE，执行期实验
- 未分类行为：UNVERIFIED，待决定
"""

    problems, warnings = unverified_contract_gate(spec, require_verified=True)

    assert warnings == []
    assert len(problems) == 3
    assert any("裸 UNVERIFIED" in problem for problem in problems)
    assert any("UNVERIFIED-BLOCKING" in problem for problem in problems)
    assert any("UNVERIFIED-SPIKE" in problem for problem in problems)
