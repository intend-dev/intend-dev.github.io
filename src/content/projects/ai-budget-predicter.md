---
title: "Discrimination analysis and fairness audit of a high-accuracy model"
courseProject: "AI Budget Predicter (Ethics)"
summary: "A budget-prediction model with 0.9885 balanced accuracy at the threshold that maximises it, a dataset-level Disparate Impact of 0.0149, and a reweighing pass that restored equal opportunity on the same test split."
order: 6
featured: true
status: completed
course: "Ethics in AI"
year: "2026"
role: "Solo build"
stack: ["Python", "scikit-learn", "AIF360", "pandas"]
metrics:
  - value: "156,317"
    label: "samples"
  - value: "0.9885"
    label: "balanced accuracy, tuned on test"
  - value: "0.0149"
    label: "dataset-level disparate impact"
  - value: "0.0000"
    label: "EOD after reweighing"
flow:
  - label: "156,317 samples"
    note: "budget data"
  - label: "Gaussian NB"
    note: "0.9885, tuned on test"
  - label: "Fairness audit"
    note: "dataset DI 0.0149, SPD -0.5831"
  - label: "Reweighing"
    note: "EOD 0.0000"
  - label: "Model card"
    note: "leak documented"
links: []
code: private
---

## Context

This was an Ethics in AI course project. The task was to build a budget-prediction model and test whether a high accuracy score could coexist with discrimination. It could. Education level is the protected attribute for this audit.

## Constraint

The fairness metrics had to cover more than one definition. The model card had to record what the metrics did not capture, including a target leak found during feature analysis.

## What I built

- A Gaussian Naive Bayes classifier over `156,317` samples, with balanced accuracy of `0.9885` at the threshold that maximises it on the 20% test split.
- A discrimination and fairness audit using Disparate Impact, statistical parity difference, equal opportunity difference, and average odds difference where the source values agreed.
- A reweighing pass that restored equal opportunity to `0.0000` on the test split, after threshold tuning.
- A model card that documents the accuracy paradox and the target leak.

## Evidence

| Metric | Result |
|---|---|
| Balanced accuracy (test split, tuned threshold) | `0.9885` |
| Disparate Impact (dataset labels) | `0.0149` |
| Statistical parity difference (dataset labels) | `-0.5831` against secondary-school graduates |
| Equal opportunity difference after reweighing | `0.0000` |
| Leak permutation importance | `0.1521` |

A Disparate Impact of `0.0149` needs its reference point: `1.0` is parity, and `0.8` is the four-fifths rule. At `0.0149` the disadvantaged group's favourable outcome rate is about `1.5%` of the reference group's rate, measured on the dataset labels. Before the protected attribute was changed to education level, one group had zero positive labels and the odds-based metrics were undefined. That is evidence of group separation, not a code error. Average odds difference is omitted because the source catalogue reports both `0.0011` and `0.0005` for it.

The threshold was selected on the same `20%` test split it is reported on, so `0.9885` is an optimistically selected figure rather than a held-out estimate of generalisation.

## What broke

The model was accurate and discriminatory at the same time. The target leak is structural: `Budget (in dollars)` stays in the feature set while the label is that same column cut at `300`, so the model can read the target off a feature. The `0.1521` permutation importance is a consequence of the leak, not the proof of it. Reweighing fixed equal opportunity on the test split; it did not make the original model fair. The model card keeps both facts.

## What I'd do next

Re-run the audit on a held-out cohort and add a calibration check, so the mitigation is tested outside the data that produced it.
