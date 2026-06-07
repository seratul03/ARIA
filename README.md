# ARIA — Autonomous Recursive Improvement Agent

> A self-improving AI system that monitors its own tools, detects weaknesses, generates better code via LLM, validates it in Docker, and deploys improvements automatically — all with Git version control and a live terminal dashboard.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                         ARIA System                             │
│                                                                 │
│  ┌──────────────┐    ┌──────────────┐    ┌───────────────────┐ │
│  │  Agent Core  │───▶│ Tool Layer   │───▶│ Metrics Collector │ │
│  │  (scheduler) │    │  (6 tools)   │    │   (SQLite)        │ │
│  └──────┬───────┘    └──────────────┘    └────────┬──────────┘ │
│         │                                          │            │
│         ▼                                          ▼            │
│  ┌──────────────┐    ┌──────────────┐    ┌───────────────────┐ │
│  │ Improvement  │◀───│ Introspection│◀───│  Weakness Report  │ │
│  │   Engine     │    │   Engine     │    │  (thresholds)     │ │
│  │  (Groq LLM)  │    └──────────────┘    └───────────────────┘ │
│  └──────┬───────┘                                               │
│         │                                                        │
│         ▼                                                        │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │                    GATEKEEPER (Immutable)                  │   │
│  │  Static AST Analysis  →  Docker Sandbox  →  Deploy/Reject │   │
│  └──────────────────────────────────────────────────────────┘   │
│         │                                                        │
│         ▼                                                        │
│  ┌──────────────┐    ┌───────────────┐   ┌───────────────────┐ │
│  │  Git Manager │    │Interactive Menu│  │   CLI Interface   │ │
│  │  (rollback)  │    │ (Questionary) │   │   (argparse)      │ │
│  └──────────────┘    └───────────────┘   └───────────────────┘ │
└─────────────────────────────────────────────────────────────────┘
```

---

## Quick Start

### 1. Prerequisites

- Python 3.11+
- Docker Desktop (running)
- A [Groq API key](https://console.groq.com) (free tier available)

### 2. Install dependencies

```bash
pip install -r requirements.txt
```

### 3. Configure environment

```bash
cp .env.example .env
# Edit .env and set your GROQ_API_KEY
```

### 4. Launch ARIA

```bash
python -m aria run
```

---

## CLI Commands

| Command | Description |
|---|---|
| `python -m aria run` | Launch the Interactive Command Center (Menu) |
| `python -m aria status` | Print current tool metrics table |
| `python -m aria improve --tool search_tool` | Manually trigger improvement for a tool |
| `python -m aria rollback --tool search_tool` | Revert a tool to its last Git version |
| `python -m aria run-tool --tool calculator_tool --input '{"expression":"2+2"}'` | Execute a tool directly |
| `python -m aria history` | Show all improvement history |

---

## Interactive Output Grading

Every time you run a tool via the Interactive Menu, ARIA uses an LLM to grade its own output. 
- Start at 10/10.
- Deduct points for minor formatting, lack of detail, or missing edge cases.
- If the score drops below **9/10**, the Autonomous Improvement Loop is triggered instantly.

---

## Tools

| Tool | Description | Improvable |
|---|---|---|
| `search_tool` | DuckDuckGo web search with HTML fallback | ✅ |
| `summarizer_tool` | LLM + extractive text summarization | ✅ |
| `calculator_tool` | Safe AST-based math evaluator | ✅ |
| `file_reader_tool` | Allowlisted file reading with path traversal protection | ✅ |
| `code_executor_tool` | Python snippet execution via Docker | ✅ |
| `weather_tool` | Open-Meteo weather (free, no auth) | ✅ |

---

## Safety Model

```
ARIA can ONLY modify: aria/tools/*.py
ARIA CANNOT modify:   aria/core/, aria/gatekeeper/, aria/metrics/, .env
```

### Layered Safety Gates

1. **Rate Limiting** — Max 5 improvement cycles/hour (configurable)
2. **Groq Rate Limiter** — Sliding window prevents 429 errors
3. **Static AST Analysis** — Blocks forbidden imports (`os`, `sys`, `subprocess`) to prevent OS-level backdoors.
4. **Docker Sandbox** — Isolated container that enforces a strict 100% test-pass rate.
5. **Hallucination Protection** — Sandbox explicitly catches and rejects hallucinated arguments or hallucinated test cases.
6. **Performance Gate** — New code must not be >50% slower than current
7. **Git Rollback** — Every change is committed; failures auto-rollback

---

## Configuration (`.env`)

| Variable | Default | Description |
|---|---|---|
| `GROQ_API_KEY` | required | Your Groq API key |
| `GROQ_MODEL` | `llama3-8b-8192` | Groq model to use |
| `SUCCESS_RATE_THRESHOLD` | `0.70` | Flag tools below this success rate |
| `LATENCY_THRESHOLD_SECONDS` | `5.0` | Flag tools above this p90 latency |
| `MAX_IMPROVEMENT_CYCLES_PER_HOUR` | `5` | Safety cycle rate limit |
| `SCHEDULER_INTERVAL_MINUTES` | `30` | Auto-check interval |
| `SANDBOX_MEMORY_LIMIT` | `256m` | Docker container memory limit |
| `GROQ_MIN_REQUEST_INTERVAL_SECONDS` | `3.0` | Min seconds between Groq calls |

---

## Recursive Improvement Loop

```
Scheduler wakes (every 30 min)
         │
         ▼
Introspection Engine scans metrics
         │
    ┌────┴────┐
    │ Weak?   │
    └────┬────┘
    Yes  │
         ▼
Improvement Engine → Groq LLM
         │
         ▼
 ┌─────────────────────────┐
 │      GATEKEEPER         │
 │                         │
 │ 1. AST Static Analysis  │
 │ 2. Docker Sandbox Tests │
 │ 3. Performance Check    │
 └───────────┬─────────────┘
        Pass │     Fail │
             ▼          ▼
          Deploy    Rollback + Log
          + Git        + Notify
          Commit
             │
             ▼
     Return to start
```

---

## Project Structure

```
ARIA/
├── aria/
│   ├── core/           Agent Core, Scheduler, Rate Limiter
│   ├── tools/          6 improvable tools + registry + base class
│   ├── metrics/        SQLite DB schema + collector
│   ├── introspection/  Weakness detection engine
│   ├── improvement/    LLM prompt builder + improvement engine
│   ├── gatekeeper/     Static validator + Docker sandbox
│   ├── versioning/     Git manager
│   └── ui/             Interactive monochromatic CLI menu
├── .env.example        Configuration template
├── Dockerfile.sandbox  Docker image for tool validation
├── requirements.txt    Python dependencies
└── README.md
```

---

## Limitations

- Cannot modify its own LLM (Groq) or core reasoning
- Cannot redesign its own architecture
- Improvement quality depends on the Groq LLM response
- Docker must be running for sandbox validation

---

## License

MIT — Educational and experimental use.
