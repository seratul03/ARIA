# ARIA — Autonomous Recursive Improvement Agent: Technical Architecture

ARIA is an advanced, autonomous AI system that not only executes tools but continuously monitors, evaluates, and improves its own capabilities and internal architectural frameworks. This document provides an exhaustive, deeply detailed technical breakdown of ARIA's internal architecture, execution workflows, safety models, and subsystem mechanics.

---

## 1. Introduction and High-Level Overview

### 1.1 The Philosophy of Recursive Meta-Improvement
Unlike traditional LLM-based agents that execute static tools, ARIA treats its own codebase as a mutable environment. The system functions on two distinct tiers of improvement:
1. **Tool-Level Improvement**: Observing individual tool execution (e.g., search functions, code executors), identifying edge cases, and generating superior tool implementations.
2. **Tool Synthesis (Self-Expansion)**: Generating entirely new tools on-the-fly from human-provided natural language specifications, expanding the agent's capability bounds.
3. **Meta-Level Improvement**: Observing system-wide architectural bottlenecks (e.g., poor prompt generation, flawed scheduling), mapping them to a self-model, and rewriting ARIA's own core orchestration code.

### 1.2 High-Level System Architecture Diagram

```mermaid
graph TD
    subgraph Core System
        AC[Agent Core & Scheduler] --> TL[Tool Layer]
        TL --> MC[Metrics Collector DB]
        AC --> IM[Improvement Engine]
    end

    subgraph Meta-Introspection Loop
        MC --> IE[Introspection Engine]
        IE --> SM[Self-Model JSON]
        SM --> MI[Meta-Improvement Engine]
    end

    subgraph Safe Execution
        IM --> GK[Gatekeeper: Tool Sandbox]
        MI --> CM[Clone Manager]
        CM --> AC2[Arena Combat: Clone vs Baseline]
        AC2 --> REF[Referee Service]
    end

    subgraph Deployment
        GK --> GM[Git Manager]
        REF --> GM
        GM --> CLI[Interactive Menu & CLI]
    end
```

---

## 2. Core Subsystems Breakdown

### 2.1 Agent Core & Scheduler (`aria/core`)
The **Agent Core** (`agent.py`) is the central brain of ARIA. It manages event queues, rate limits, orchestrates execution cycles, and publishes status updates to the TUI (Terminal User Interface).

- **Event Queue System**: Decouples the UI from execution by publishing `AgentEvent` objects (e.g., `CYCLE_STARTED`, `SANDBOX_VALIDATION`, `ROLLED_BACK`) to a thread-safe queue.
- **Cycle Rate Limiter**: Enforces strict caps on improvement attempts (e.g., max 5 improvements per hour) independent of external API rate limits.
- **Execution Orchestration**: The `run_improvement_cycle()` method triggers the end-to-end pipeline: Introspection -> Generation -> Arena Validation -> Review/Deploy.
- **Post-Deployment Monitoring**: A background process that checks the health of deployed improvements. If a tool's success rate regressions are detected post-deployment, the Agent Core automatically rolls the codebase back to the prior stable Git tag.

### 2.2 Metrics Collector and Tracing (`aria/metrics`)
All tool executions are heavily monitored and logged to an internal SQLite database (`aria.db`).

- **Tool Stats**: Tracks execution latency, memory footprint, token usage, and pass/fail success rates.
- **Cycle Traces**: Records the entire lifespan of an improvement cycle, logging prompts sent to the LLM, the raw generated code, test outcomes, and final deployment status.
- **Review Queue**: Stores generated improvements that passed tests but require manual human authorization before Git deployment.

### 2.3 Introspection Engine (`aria/introspection`)
The Introspection Engine bridges the gap between historical metrics and actionable improvements.

- **Weakness Detection**: Analyzes SQLite traces to find the "worst-performing" tool based on weighted scores (p90 latency, pass rate).
- **The Self-Model (`self_model.json`)**: During a Meta-Introspection cycle, an LLM parses hundreds of cycle traces to build a structural understanding of ARIA's flaws. This is persisted to `self_model.json`, which tracks "known_weaknesses" and "failure_patterns" per core component (e.g., `improvement_engine`, `gatekeeper`).
- **Clone Lifecycle**: When a meta-improvement is triggered, ARIA cannot safely alter its live state. Instead, the `clone_manager` duplicates the entire ARIA repository into a temporary directory, applies the architectural change to the clone, and executes an Arena Combat evaluation.

### 2.4 Improvement Engine (`aria/improvement`)
Responsible for reading weaknesses and generating candidate code via LLMs (Groq).

- **Prompt Construction**: Dynamically assembles prompts that include the tool's source code, execution history, identified flaws, and the specific failure cases.
- **Adversarial Test Generation**: Before code is written, ARIA generates a suite of "Tier 3" adversarial test cases designed specifically to exploit the identified weakness. These tests are cryptographically signed to prevent the newly generated tool code from altering the tests.

---

## 3. The 7-Layer Defense System (Gatekeeper)

The **Gatekeeper** (`aria/gatekeeper`) is ARIA's immune system. Because ARIA executes untrusted, AI-generated Python code, strict isolation is paramount.

### Layer 1: Rate Limiting
Strict limits are placed on improvement frequency to prevent recursive runaway loops (e.g., 5 tool cycles/hour, 1 meta cycle/day).

### Layer 2: Static AST Analysis
Before executing any generated code, the static validator (`validator.py`) parses the Abstract Syntax Tree (AST) of the candidate source. It enforces:
- A strict whitelist of permitted imports.
- Blocked modules: `os`, `sys`, `subprocess`, `shutil` — preventing OS-level backdoors.

### Layer 3: Docker Sandbox Isolation
The candidate code is packaged with its adversarial test suite and executed inside a fully isolated Docker container (`python:3.11-slim`).
- **Network Isolation**: The container has zero network access. External APIs are strictly mocked.
- **The `mock_apis` Framework**: To prevent LLMs from hardcoding fake responses inside their production `run()` logic to cheat the sandbox, new tools must implement a `mock_apis(self, respx_mock)` hook. The Sandbox runner injects a `respx` router into this hook to mock API endpoints statically before executing the tests.
- **Resource Limits**: Strict memory limits (e.g., 256MB) and CPU quotas prevent malicious or accidental infinite loops.

### Layer 4: Cryptographically Signed Tests
The adversarial test cases generated by ARIA are signed using HMAC-SHA256 (`session_token`). When injected into the Docker sandbox, the runner verifies this signature. This guarantees the LLM hasn't hallucinated easy test cases to artificially pass validation.

### Layer 5: Arena Combat (Clone vs. Baseline)
The new code is evaluated, but that is not enough. The *current* version of the tool (Baseline) is also evaluated against the *exact same* adversarial test suite.

### Layer 6: The Referee Protocol & Fitness Gate
A specialized **Referee Service** (`referee`) runs outside the container. It receives the JSON results of both the Baseline and the Clone.
- It computes a weighted **Fitness Score**: `(Pass Rate * Weight) - (Latency * Weight) - (Memory * Weight) - (Tokens * Weight)`
- **The Rule**: The Clone must mathematically outscore the Baseline by a predefined delta. If it does not perform strictly better, or if it hallucinates, the Clone is ruthlessly discarded.

### Layer 7: Git Rollback Integration
Every successful deployment is committed to Git with auto-generated tags (`pre_improvement_X`, `post_improvement_X`). If post-deployment monitoring flags a regression, the system executes an automated `git checkout` to restore stability.

---

## 4. Execution Flow: The Meta-Improvement Loop

A standard tool improvement cycle flows precisely as follows:

1. **Trigger**: Scheduler triggers a cycle based on time or user CLI request.
2. **Introspect**: `IntrospectionEngine` queries `aria.db` to locate the tool with the lowest success rate.
3. **Adversarial Synthesis**: Generates edge-case tests targeting the tool's weaknesses, signing them securely.
4. **Generate**: Groq LLM generates `candidate_tool.py` fixing the logic.
5. **Static Check**: Validator ensures no malicious imports exist in `candidate_tool.py`.
6. **Sandbox (Baseline)**: Current tool runs against the adversarial tests in Docker.
7. **Sandbox (Clone)**: Candidate tool runs against the adversarial tests in Docker.
8. **Combat Evaluation**: The Referee compares outputs. If `Clone_Score > Baseline_Score`, proceed.
9. **Human Review Gate**: If `REQUIRE_HUMAN_REVIEW=true`, the code enters a pending state in the database.
10. **Deploy**: Code is written to the host filesystem. Git commits the change. The tool registry is hot-reloaded.

### 4.1 Meta-Introspection Flow (Architectural Changes)
If the cycle targets ARIA itself:
1. `meta.py` generates a clone of the entire repo.
2. LLM proposes an architectural shift (e.g., rewriting `aria/core/scheduler.py`).
3. Change applied to the clone.
4. Clone spins up and executes an internal improvement cycle as a benchmark.
5. If the clone successfully improves a benchmark tool faster/better than the host ARIA, the clone's architectural change is extracted and deployed to the host.

### 4.2 Tool Synthesis Flow (Self-Expansion)
When ARIA encounters a task it lacks the capability for, the `synthesize` engine engages:
1. **Specification Phase**: User provides a natural language specification (e.g., "Fetch real-time Bitcoin prices via Coingecko").
2. **Generation**: The LLM synthesizes a brand-new subclass of `BaseTool`, implementing production code in `run()` and mock HTTP responses via `mock_apis()`.
3. **Dual-Key Orchestration**: Because synthesis requires large generation windows, ARIA routes synthesis prompts through a dedicated `SYNTHESIS_GROQ_API_KEY` to protect the primary orchestration key from rate-limiting.
4. **Sandbox Verification**: The synthesized tool runs its own `test_cases()` inside the isolated Docker sandbox without baseline comparison (since no baseline exists).
5. **Deployment**: If tests pass without network leaks or structure violations, the tool is permanently saved to `aria/tools/` and instantly becomes available in the registry for future execution.

---

## 5. Tool Architecture

Tools in ARIA extend `BaseTool` (`aria/tools/base.py`). 

```python
class BaseTool:
    @property
    def name(self) -> str: ...
    @property
    def description(self) -> str: ...
    def run(self, input_data: dict) -> ToolResult: ...
    def test_cases(self) -> list[TestCase]: ...
```

Tools carry their own self-validating `test_cases()`. This forces the LLM to write tests whenever it modifies a tool, ensuring high test coverage and protecting against regressions during future iterations.

---

---

## 6. The Memory Subsystem (Long-Term Retention & Analytics)

ARIA possesses a highly sophisticated memory architecture (`aria/memory`) designed to retain knowledge across agent restarts and thousands of improvement cycles. Instead of starting from scratch every run, ARIA recalls past mistakes and proven fixes to accelerate learning.

### 6.1 Failure History & Improvement Tracking
- **`failure_history`**: Every time a tool fails, ARIA logs the traceback, error message, and context.
- **`improvement_history`**: Logs every generated fix, whether it succeeded or failed, and its "Fitness Delta".

### 6.2 Intelligent Retrieval (Semantic & Execution Context)
When an improvement cycle begins, ARIA queries its memory using:
1. **Traceback Signatures**: Exact matches to identical stack traces.
2. **Semantic Similarity**: Fuzzy matching of error messages using rapid NLP techniques.
3. **Cross-Pollination**: If `weather_tool` fails with a `KeyError`, ARIA can recall a successful fix for a `KeyError` in `calculator_tool` and apply the same logic.

### 6.3 Memory Compression & Clustering
To prevent the database from growing unbounded with duplicate errors:
- **`failure_patterns`**: A background compression engine (`compression.py`) continually scans the database, clustering identical failure signatures into unified "patterns".
- **Temporal Regression Detection**: If ARIA deploys a fix for a pattern, it is marked as `resolved`. If the exact error occurs again *after* the deployment timestamp, ARIA detects the regression and automatically degrades the pattern back to `active`.

### 6.4 Memory Ranking
Not all memories are equally valuable. ARIA dynamically ranks historical context using a weighted scoring algorithm (`memory_score`):
- **Reliability (40%)**: How many subsequent improvement cycles successfully reused this fix?
- **Frequency (30%)**: How often does this error actually happen in production? (Logarithmic scaling).
- **Recency (30%)**: Is this memory from yesterday, or three months ago? (Exponential decay half-life).

### 6.5 Memory Dashboard & Analytics
The CLI and TUI provide real-time analytics into ARIA's brain:
- **Pain Score**: ARIA mathematically computes the "worst tool" in the system by multiplying its failure count by an unreliability penalty (tools whose fixes don't survive get penalized).
- **Commands**: Accessible via `python -m aria memory`, including `--failures`, `--fixes`, `--worst-tool`, and `--reliability`.

---

## 7. The Root Cause Analysis Subsystem (Phase 2)

Built on top of the Memory Subsystem, the Root Cause Analysis module (`aria/rootcause`) aggregates raw stack traces and isolated tool metrics into high-level systemic diagnoses.

### 7.1 Taxonomy and Classification
Instead of treating every exception as a unique event, ARIA maps failures into a fixed taxonomy (e.g., `Network`, `Authentication`, `Logic Error`, `Timeout`).
- **Heuristic Classification**: Fast, regex-based matching for common exceptions (e.g., matching `TimeoutError` to `Timeout`).
- **LLM Classification**: Complex or ambiguous tracebacks are sent to Groq for classification, bounded by a strict rate limit (`MAX_ROOTCAUSE_LLM_CALLS_PER_CYCLE`) to prevent runaway API costs on unclassified traces.

### 7.2 Failure Clustering & Pattern Extraction
ARIA continuously scans classified failure patterns to find overlapping vectors:
- **`root_cause_clusters`**: Groups identical failure categories that occur close together or exhibit high frequency.
- **`architectural_patterns`**: The LLM analyzes the raw error context of a cluster and synthesizes human-readable systemic flaws (e.g., "The weather tool and search tool both lack timeout retries, leading to systemic brittleness").

### 7.3 The Hypothesis Pipeline
When an architectural pattern is extracted, ARIA generates a **Hypothesis** (`aria.rootcause.hypotheses.generate_hypotheses`).
- A hypothesis is an actionable proposal to fix the systemic flaw.
- **Hijacking the Scheduler**: If a hypothesis scores a high enough confidence, the `select_next_target()` logic bypasses the default "worst-performing tool" selection and instead triggers an improvement cycle targeting the tools involved in the hypothesis.
- **The `## DIRECTIVE`**: The Introspection Engine explicitly injects the proposed hypothesis into the Groq improvement prompt, commanding the LLM to implement the specific architectural fix.

### 7.4 Root Cause Synthesis (The `why` Command)
ARIA synthesizes all layers of the root cause module—from basic category breakdowns to active architectural patterns and hypothesis attempt counts—into a unified data structure.
- Accessible via the CLI (`python -m aria why`).
- Injected natively into the Meta-Introspection loop (`self_model.json`), allowing ARIA to explicitly state its own systemic flaws when analyzing its internal health.

---

## 8. Summary

ARIA represents a paradigm shift in agent design. By treating the agent framework itself as a fluid, optimizable construct, and surrounding that mutability with extreme, mathematically grounded safety constraints (Gatekeeper, Docker Sandboxing, Git Rollbacks, and Long-Term Memory Compression), ARIA achieves safe, persistent, autonomous self-evolution.
