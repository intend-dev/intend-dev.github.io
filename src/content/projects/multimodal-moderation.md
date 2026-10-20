---
title: "Moderation agents that return a typed decision and a reason"
courseProject: "Multimodal Content Moderation"
summary: "A policy-as-schema pipeline: text, image, video, and audio agents return a typed decision with a rationale field before any message reaches the model."
order: 5
status: completed
course: "Generative AI"
year: "2026"
role: "Solo build"
stack: ["Python", "LLM APIs", "pytest"]
metrics:
  - value: "100%"
    label: "text checks"
  - value: "88.3%"
    label: "image checks"
  - value: "92.5%"
    label: "extended text policy checks"
  - value: "19"
    label: "schema tests"
flow:
  - label: "Message"
    note: "text, image, video, audio"
  - label: "Typed agents"
    note: "policy schema"
  - label: "Rationale fields"
    note: "in the schema"
  - label: "Allow or block"
    note: "typed decision"
  - label: "Trace"
    note: "one span per call"
links: []
code: private
---

## Context

This was a Generative AI course project. Every text, image, video, and audio message had to pass moderation before it reached the language model. The block decision was the easy part. Making the decision auditable and testable was the work.

## Constraint

The output had to fit a typed policy schema, with a rationale field on every result. The evaluation harness retried a failing run up to `10` attempts; the application did not retry. A failed moderation call could not silently pass the message through.

## What I built

- Typed moderation agents for each modality, all returning the same policy schema.
- A rationale field on every result, with a default when the model omits one.
- A policy layer that applies the schema before the message reaches the model.
- Per-run traces of every moderation operation.
- A `19`-test schema suite that runs in `0.09` seconds.

## Evidence

| Check | Result |
|---|---|
| Text checks | `100%` of 60 assertions over `15` runs of three fixtures |
| Image checks | `88.3%`, 53 of 60 assertions |
| Extended text policy checks | `92.5%`, 111 of 120 assertions |
| Tests | `19` in `0.09` seconds |
| Retries | up to `10` per run in the evaluation harness |

An assertion passes when a run's output matches the expected outcome for its fixture, so these percentages are assertion pass rates over repeated fixtures, not accuracy over independent examples. The image result is reported as measured; the evaluation keeps the failing cases rather than rounding the score up.

## What broke

The model was inconsistent on image checks. The evaluation reports the `88.3%` result and the cases behind it rather than rounding up.

## What I'd do next

Replace the fixed retry count in the evaluation harness with a per-modality budget, and add a calibration report, so the threshold comes from measured error rates rather than a default.
