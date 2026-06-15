from enum import Enum
import re

class RootCauseCategory(str, Enum):
    VALIDATION    = "Validation"
    NETWORK       = "Network"
    LOGIC         = "Logic"
    CONCURRENCY   = "Concurrency"
    PERFORMANCE   = "Performance"
    SECURITY      = "Security"
    CONFIGURATION = "Configuration"

CATEGORY_DESCRIPTIONS = {
    RootCauseCategory.VALIDATION: "Bad/missing/malformed input not caught before use (type errors, missing keys, malformed JSON/HTML from external sources).",
    RootCauseCategory.NETWORK: "Failures related to external connectivity: timeouts, DNS, connection resets, HTTP error codes, rate limiting from third parties.",
    RootCauseCategory.LOGIC: "Incorrect program logic: wrong conditionals, off-by-one, incorrect algorithm, wrong assumptions about data shape.",
    RootCauseCategory.CONCURRENCY: "Race conditions, deadlocks, shared-state corruption, ordering issues in async/threaded code.",
    RootCauseCategory.PERFORMANCE: "Correct but too slow / too memory-hungry — timeouts caused by the tool's own inefficiency rather than external factors.",
    RootCauseCategory.SECURITY: "Anything touching the Gatekeeper's concerns: forbidden imports, sandbox escapes, injection risks, credential handling.",
    RootCauseCategory.CONFIGURATION: "Missing/incorrect environment variables, misconfigured paths, version mismatches, Docker/sandbox setup issues.",
}

# Heuristic rules: (compiled regex over "error_type error_message", category, confidence)
# Order matters — first match wins. Confidence >= HEURISTIC_THRESHOLD short-circuits the LLM call.
HEURISTIC_THRESHOLD = 0.65

HEURISTIC_RULES: list[tuple[re.Pattern, RootCauseCategory, float]] = [
    (re.compile(r"Timeout|ConnectionError|ConnectionReset|DNS|HTTPError|429|503"), RootCauseCategory.NETWORK, 0.75),
    (re.compile(r"Permission|Forbidden import|sandbox|escape|injection|os\.|subprocess"), RootCauseCategory.SECURITY, 0.8),
    (re.compile(r"KeyError|AttributeError|IndexError"), RootCauseCategory.LOGIC, 0.6),
    (re.compile(r"TypeError|ValueError|JSONDecodeError|ValidationError"), RootCauseCategory.VALIDATION, 0.65),
    (re.compile(r"Deadlock|RaceCondition|asyncio\.|ThreadError|Lock"), RootCauseCategory.CONCURRENCY, 0.7),
    (re.compile(r"MemoryError|MemoryLimit|too slow|p90|latency"), RootCauseCategory.PERFORMANCE, 0.65),
    (re.compile(r"EnvironmentError|MissingEnvVar|FileNotFoundError.*\.env|ModuleNotFoundError"), RootCauseCategory.CONFIGURATION, 0.7),
]
