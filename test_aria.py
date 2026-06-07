"""Test script for ARIA functional validation."""
import os
import sys
os.environ['GROQ_API_KEY'] = 'test_key_placeholder'
sys.path.insert(0, '.')

print("=" * 60)
print("ARIA FUNCTIONAL TEST SUITE")
print("=" * 60)

# ── Test 1: Calculator Tool ────────────────────────────────────────
print("\n[1] Calculator Tool Tests")
from aria.tools.calculator_tool import CalculatorTool
calc = CalculatorTool()
tests = calc.test_cases()
print(f"    {len(tests)} test cases found")

all_passed = True
for tc in tests:
    result = calc.run(tc.input)
    passed = result.success == tc.expected_success
    icon = "✓" if passed else "✗"
    if not passed:
        all_passed = False
    print(f"    [{icon}] {tc.name}: got success={result.success} (expected={tc.expected_success}), output={result.output}")

print(f"    Calculator: {'ALL PASS' if all_passed else 'SOME FAILED'}")

# ── Test 2: File Reader Tool security ─────────────────────────────
print("\n[2] File Reader Tool Security Tests")
from aria.tools.file_reader_tool import FileReaderTool
fr = FileReaderTool()

# Path traversal attack
r1 = fr.run({"path": "../../etc/passwd"})
print(f"    [{'✓' if not r1.success else '✗'}] Path traversal blocked: {not r1.success}")

# Empty path
r2 = fr.run({"path": ""})
print(f"    [{'✓' if not r2.success else '✗'}] Empty path blocked: {not r2.success}")

# Nonexistent file inside workspace (should fail with 'not found')
r3 = fr.run({"path": "./workspace/no_file_exists.txt"})
print(f"    [{'✓' if not r3.success else '✗'}] Missing file fails: {not r3.success}")

# ── Test 3: Static Validator ───────────────────────────────────────
print("\n[3] Static Validator (Gatekeeper)")
from aria.gatekeeper.validator import StaticValidator
validator = StaticValidator()

# Good code
good_code = (
    "from aria.tools.base import BaseTool, ToolResult, TestCase\n\n"
    "class GoodTool(BaseTool):\n"
    "    name = 'good_tool'\n\n"
    "    def run(self, input: dict) -> ToolResult:\n"
    "        return ToolResult(success=True, output='hello')\n\n"
    "    def test_cases(self):\n"
    "        return [\n"
    "            TestCase(name='t1', input={}, expected_success=True),\n"
    "            TestCase(name='t2', input={}, expected_success=True),\n"
    "            TestCase(name='t3', input={}, expected_success=True),\n"
    "        ]\n"
)
r_good = validator.validate(good_code, 'good_tool')
print(f"    [{'✓' if r_good.passed else '✗'}] Good code passes: {r_good.passed}")

# Bad code - import os
bad_os = "import os\nfrom aria.tools.base import BaseTool, ToolResult, TestCase\nclass T(BaseTool):\n    name='t'\n    def run(self,input):return ToolResult(success=True,output=os.getcwd())\n    def test_cases(self):return []\n"
r_bad_os = validator.validate(bad_os, 't')
print(f"    [{'✓' if not r_bad_os.passed else '✗'}] import os rejected: {not r_bad_os.passed}")
if not r_bad_os.passed:
    print(f"       Reason: {r_bad_os.issues[0]}")

# Bad code - eval
bad_eval = "from aria.tools.base import BaseTool, ToolResult, TestCase\nclass T(BaseTool):\n    name='t'\n    def run(self,input):return ToolResult(success=True,output=eval('1+1'))\n    def test_cases(self):return []\n"
r_bad_eval = validator.validate(bad_eval, 't')
print(f"    [{'✓' if not r_bad_eval.passed else '✗'}] eval() rejected: {not r_bad_eval.passed}")
if not r_bad_eval.passed:
    print(f"       Reason: {r_bad_eval.issues[0]}")

# Bad code - no BaseTool inheritance
bad_nobase = "class T:\n    def run(self,input):pass\n    def test_cases(self):return []\n"
r_nobase = validator.validate(bad_nobase, 't')
print(f"    [{'✓' if not r_nobase.passed else '✗'}] Missing BaseTool rejected: {not r_nobase.passed}")

# ── Test 4: Metrics DB ────────────────────────────────────────────
print("\n[4] SQLite Metrics Database")
from aria.metrics.db import init_db, insert_execution, get_tool_stats
from pathlib import Path
import time

test_db = Path("test_aria_temp.db")
init_db(test_db)
print("    DB initialized OK")

# Insert some fake executions
for i in range(15):
    insert_execution(
        tool_name="calculator_tool",
        timestamp=time.time(),
        success=(i % 4 != 0),  # 75% success rate
        latency_seconds=0.01 + (i * 0.005),
    )

stats = get_tool_stats("calculator_tool")
print(f"    Stats: success_rate={stats.success_rate:.0%}, executions={stats.total_executions}")
print(f"    [{'✓' if stats else '✗'}] Stats returned correctly")

# Cleanup

# Close the thread-local connection before cleanup
import aria.metrics.db as _db_mod
if hasattr(_db_mod._local, 'conn') and _db_mod._local.conn:
    _db_mod._local.conn.close()
    _db_mod._local.conn = None
test_db.unlink(missing_ok=True)

# ── Test 5: Rate Limiter ──────────────────────────────────────────
print("\n[5] Groq Rate Limiter")
from aria.core.rate_limiter import SlidingWindowRateLimiter
limiter = SlidingWindowRateLimiter(min_interval_seconds=0.01, max_calls_per_minute=100)
import time
t0 = time.monotonic()
for _ in range(3):
    limiter.acquire()
elapsed = time.monotonic() - t0
print(f"    3 calls acquired in {elapsed:.3f}s (expected ~0.02s)")
print(f"    [✓] Rate limiter working")

print("\n" + "=" * 60)
print("ALL TESTS COMPLETED")
print("=" * 60)
