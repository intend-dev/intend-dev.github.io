---
title: "An AI development harness with a run ID behind every result"
courseProject: "AI-Assisted Development Harness"
summary: "Four Claude systems run and captured: two against the live API, one configuration validator, one replay of a recorded response. 127 tests, a 56.46% context cut, and an artefact for every claim."
order: 2
status: completed
course: "AI Engineering with Claude"
year: "2026"
role: "Solo build"
stack: ["Python", "Claude API", "pytest"]
metrics:
  - value: "127"
    label: "tests green"
  - value: "56.46%"
    label: "context cut"
  - value: "965 B"
    label: "hot state, 5,120 B cap"
  - value: "4"
    label: "systems run"
flow:
  - label: "Four systems"
    note: "claims, context, config, monitoring"
  - label: "Run and test"
    note: "isolated environments"
  - label: "Artifacts"
    note: "budget, traces, hot state"
  - label: "Reflection brief"
    note: "every claim cited"
links: []
code: private
---

## Context

This was the capstone for AI Engineering with Claude. The brief defined four reference systems: a claims intake agent, a retail support context strategy, a Claude Code configuration for a monorepo, and a multi-shift quality monitor. I was not asked to rebuild them. I had to run them, capture what they produced, and defend the architecture in a brief where every claim pointed to an artefact.

## Constraint

Every claim needed a test log, a `budget.json`, a trace, or a hot-state file. The four systems pinned incompatible SDK versions, so each ran in its own virtual environment. Total API spend had to stay between `$1` and `$5`.

## What I built

- Four isolated environments, each with its own dependencies and fixture set.
- A run-and-capture pass for each system: pytest output, summary files, traces, queues, and escalation logs.
- A context-budget measurement: `38,708` tokens before assembly and `16,855` after, a `56.46%` cut.
- A hot-state measurement: `965` bytes against a `5,120`-byte cap in the multi-shift monitor. That is the state the monitor carries between shifts.
- A reflection brief that cites run IDs, token counts, outcomes, and file paths from the captured artefacts.

## Evidence

| Check | Result |
|---|---|
| Tests | `127` green (`29` + `30` + `35` + `33`) |
| Context | `38,708` to `16,855` tokens, `56.46%` cut |
| Hot state | `965` bytes against a `5,120`-byte cap |
| Spend | about `$4.5` total |
| Perturbation | policy-violating claim routed at confidence `0.8` |

## What broke

A deliberate perturbation showed that the harness enforced protocol but not policy. The run still completed, which was the finding. I wrote the code-side fix into the brief instead of quietly patching the harness and presenting a clean run.

Protocol checks passed. The missing piece was a policy assertion: the system could follow the procedure and still accept a request it should have rejected.

The official run of the claims agent also failed as a run. Three of eight claims routed, none escalated, five never terminated, and the process exited `1`. The Opus sweep completed eight of eight afterwards, and the brief records the shortfall instead of publishing only the clean sweep.

## What I'd do next

Move the policy assertion into the harness test suite. A protocol-compliant but policy-wrong run should fail a test instead of relying on the author to notice it in a brief.
