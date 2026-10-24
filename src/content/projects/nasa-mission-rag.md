---
title: "A NASA RAG system that forces a citation on every sentence"
courseProject: "NASA Mission RAG"
summary: "Answers over 16,497 NASA chunks, with a balanced two-pass retriever, a citation on every sentence, and a RAGAS loop in the app."
order: 3
featured: true
status: completed
course: "Generative AI"
year: "2026"
role: "Solo build"
stack: ["Python", "ChromaDB", "OpenAI", "RAGAS", "Streamlit"]
metrics:
  - value: "16,497"
    label: "chunks indexed"
  - value: "0.75"
    label: "faithfulness, stored answers"
  - value: "0.7447"
    label: "answer relevancy, stored answers"
  - value: "2,470"
    label: "lines across 5 modules"
flow:
  - label: "NASA corpus"
    note: "about 6 MB"
  - label: "Chunk and embed"
    note: "16,497 chunks"
  - label: "Two-pass retrieve"
    note: "balance across missions"
  - label: "Citation guardrail"
    note: "a citation on every sentence"
  - label: "Answer and RAGAS"
    note: "loop in the app"
links: []
code: private
---

## Context

This was a Generative AI course project. The corpus was about `6 MB` of NASA mission transcripts and technical reports. The system had to answer questions with citations a reader could open and check, not with plausible prose. The implementation is `2,470` lines across `5` modules.

## Constraint

The evaluation had to run with the system, not after it. Retrieval had to cover multiple missions without letting one mission dominate every answer. Every answer had to carry citations a reader could open and check before the answer reached the screen.

## What I built

- A ChromaDB embedding pipeline over `16,497` chunks.
- A balanced two-pass retriever that replaced single-mission retrieval bias when a question spans several missions.
- A citation guardrail that normalises citations, forces one onto every sentence, and drops any index that is not in the retrieved set. It checks that a citation exists and points into the retrieved context; it does not check that the cited passage supports the sentence.
- A Streamlit chat interface with conversation history.
- A RAGAS loop inside the app, plus a batch evaluation over a fixed question set rather than a notebook run after the fact.

## Evidence

| Metric | Result |
|---|---|
| Faithfulness (six stored answers) | `0.75` |
| Answer relevancy (six stored answers) | `0.7447` |
| Corpus | about `6 MB`, `16,497` chunks |

Both scores are of answers stored in the question file, scored as if they were the system's output; they are not scores of answers the RAG system generated. No live system-level run was saved, so the report shows these as stored-answer evaluation rather than as the system's answer quality.

Discarded evaluation result: context precision came out at `0.9999` because the implementation passed the same context list as both the retrieved and the reference contexts, so the metric compared the list with itself. It says nothing about retrieval and is not reported as a result.

## What broke

The first retriever over-weighted one mission. The answer looked grounded while the context was narrow. The two-pass retriever addressed the coverage problem by balancing retrieval across missions. No per-mission retrieval measurement was saved, so the fix is evidenced in the code rather than in a before-and-after figure.

## What I'd do next

Add a reranker and an answer-level entailment check, then measure system-level faithfulness against a proper retrieval metric rather than the discarded context-precision number.
