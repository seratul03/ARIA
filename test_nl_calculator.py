"""Quick smoke test for the CalculatorTool with natural language inputs."""
from aria.tools.calculator_tool import CalculatorTool

tool = CalculatorTool()

tests = [
    ("add 3 and 5", 8.0),
    ("five plus three", 8.0),
    ("what is 10 plus 5", 15.0),
    ("square root of 16", 4.0),
    ("2 + 3", 5.0),
    ("twenty times three", 60.0),
    ("subtract 3 from 10", 7.0),
    ("20 percent of 250", 50.0),
    ("multiply 4 by 6", 24.0),
    ("2 to the power of 10", 1024.0),
    ("what is five times ten", 50.0),
    ("calculate 100 divided by 4", 25.0),
    ("7 times 8", 56.0),
    ("2 + 3 * sin(pi/2)", 5.0),
    ("10 / 0", None),  # expect failure
]

print("=" * 60)
print("  ARIA Calculator Tool — Natural Language Smoke Test")
print("=" * 60)

passed = 0
failed = 0

for expr, expected in tests:
    result = tool.run({"expression": expr})
    if expected is None:
        ok = not result.success
    else:
        ok = result.success and abs(result.output - expected) < 0.001
    status = "PASS" if ok else "FAIL"
    if ok:
        passed += 1
    else:
        failed += 1
    out = result.output if result.success else result.error
    print(f"  [{status}]  {expr:35s} -> {out}")

print("-" * 60)
print(f"  Results: {passed} passed, {failed} failed out of {len(tests)}")
print("=" * 60)
