---
title: "A code review orchestrator for live pull requests"
courseProject: "Enterprise Multi-Agent Code Review Orchestrator"
summary: "Two MCP servers, three specialist reviewers per changed file, and a Zod-validated report built from live GitHub pull requests against a reconstructed repository fixture."
order: 1
status: completed
course: "AI Engineering with Claude"
year: "2026"
role: "Solo build"
stack: ["TypeScript", "Claude Agent SDK", "MCP", "Zod"]
metrics:
  - value: "3"
    label: "specialist reviewers"
  - value: "2"
    label: "MCP servers"
  - value: "3"
    label: "pull requests"
  - value: "56"
    label: "unit tests"
flow:
  - label: "Pull request"
    note: "GitHub MCP"
  - label: "Orchestrator"
    note: "Zod output contract"
  - label: "ESLint MCP"
    note: "configured, not invoked"
  - label: "3 reviewers"
    note: "quality, tests, refactoring"
  - label: "Validated report"
    note: "JSON, Markdown, HTML"
links: []
code: private
---

## Context

This was the capstone for AI Engineering with Claude. The brief named a public repository and three pull requests to review. The repository was gone by the time I started: the URL returned `404`. I recorded that check, rebuilt a fixture from the brief's description in a private repository, and reviewed three pull requests against it.

## Constraint

The reviews had to run against live pull requests, not local diffs. Every run had a hard dollar cap, had to survive one reviewer failing, and had to produce output that another program could validate. A prose report was not enough.

## What I built

- An orchestrator on the Claude Agent SDK. It takes an async generator as input and declares a Zod `outputFormat`, so a malformed report fails validation instead of reaching the next stage.
- Two MCP servers: GitHub for pull-request metadata and changed files, and ESLint, configured but not invoked in any recorded run.
- Three subagents per changed file: code quality, test coverage, refactoring. Each gets a least-privilege tool allowlist and a file-type skill, so a reviewer cannot wander outside its job.
- A report aggregator that writes JSON, Markdown, and HTML, plus a trace sidecar rendered as a Mermaid sequence diagram.
- A sliding-window rate limiter, retries with exponential backoff and jitter around the report write, timeouts, and a per-run cap of `$2.00`.

## Evidence

| Check | Result |
|---|---|
| Recorded runs | `9` run IDs |
| Pull requests | `3` |
| Report scores | `82`, `58`, `42` of `100` |
| Unit tests | `56` |
| Per-run spend | `$0.18` to `$0.80`, cap `$2.00` |

The reports scored the three pull requests `82`, `58`, and `42` out of `100`. Nine run IDs are recorded, seven of them across those pull requests, so every number above can be traced to a run.

One report's summary counts `14` high-priority test paths while its per-file arrays hold `13`. The arrays are the source of truth. I left the summary drift in the saved artefact instead of editing the report after the fact.

## What broke

The fixture was gone. I documented the `404` and checked that the files analysed in the replacement repository match the pull-request heads apart from line endings.

A reviewer can fail without killing the run: the report is degraded but still produced. An MCP initialisation failure is different; it stops the run before any spend.

The summary drift above is the other failure. The model can describe its own output incorrectly, even when the underlying findings validate.

## What I'd do next

Add a golden report per fixture and a diff gate. Then summary drift fails a test instead of depending on a reader to notice it.
