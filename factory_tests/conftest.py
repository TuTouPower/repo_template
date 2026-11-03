"""工厂集成测试路径：把产物工具链加入每个 worker 的 sys.path。

本目录只在模板工厂运行，不随 repo/ 产物分发。测试经 sys.path 导入
repo/.repo_template/scripts 的脚本模块与 repo/.repo_template/tests 的 review_support。
"""

import sys
from pathlib import Path

TEMPLATE_DIR = Path(__file__).resolve().parents[1] / "repo" / ".repo_template"

for _path in (TEMPLATE_DIR / "scripts", TEMPLATE_DIR / "tests"):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))
