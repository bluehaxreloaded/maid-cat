"""Test setup. Tests run offline: no Discord, no database, and never the real constants.py.

A stand-in constants module is built from constants.py.example, with blank IDs set to 0 and the database
left out, so the tests behave the same locally and on GitHub.
"""

import re
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _test_constants() -> types.ModuleType:
    source = (ROOT / "constants.py.example").read_text(encoding="utf-8")
    # "NAME = # comment" or "NAME =" with nothing set becomes "NAME = 0"
    source = re.sub(r"^(\w+)\s*=\s*(#.*)?$", r"\1 = 0 \2", source, flags=re.M)
    module = types.ModuleType("constants")
    module.__file__ = str(ROOT / "constants.py.example")
    exec(compile(source, module.__file__, "exec"), module.__dict__)
    module.DATABASE = None  # never touch a real database from a test
    module.SECINFO_RETAIL_N = None
    return module


sys.modules["constants"] = _test_constants()
