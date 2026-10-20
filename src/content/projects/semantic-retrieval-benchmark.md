---
title: "A retrieval benchmark that reports the failure"
courseProject: "Semantic Retrieval Benchmark"
summary: "2,000 queries, three retrievers, and a from-scratch MRR evaluator. A 10-configuration parameter search lifted Word2Vec from 0.0232 to 0.6985; MPNet still reached 0.9297."
order: 4
status: completed
course: "Deep Learning"
year: "2026"
role: "Solo build"
stack: ["Python", "PyTorch", "gensim", "sentence-transformers"]
metrics:
  - value: "2,000"
    label: "queries"
  - value: "0.9297"
    label: "MPNet MRR"
  - value: "0.6738"
    label: "BM25 MRR"
  - value: "+38%"
    label: "over BM25"
flow:
  - label: "Same 2,000 queries"
    note: "Natural Questions"
  - label: "Three retrievers"
    note: "BM25, Word2Vec, MPNet"
  - label: "From-scratch MRR"
    note: "Recall@k and MRR"
  - label: "Comparison"
    note: "+38% over BM25"
links: []
code: private
---

## Context

This was a Deep Learning course project. The question was where semantic retrieval actually pays over keyword search, and where it does not.

## Constraint

Every retriever had to use the same `2,000` queries and the same relevance labels. The metrics had to be implemented from scratch rather than imported from a library that could hide the ranking behaviour. The best Word2Vec configuration was selected on the same 2,000 queries used for the comparison, and retrieval ran over the 2,440 documents that appear in the labels, so the figures compare retrievers rather than measure a full-corpus benchmark.

## What I built

- A BM25 retriever and a from-scratch evaluator for Recall@k and MRR.
- A Word2Vec retriever trained on the corpus, with document vectors built by averaging word embeddings.
- A `10`-configuration parameter search over Word2Vec parameters, sampled from a 48-point grid.
- An MPNet sentence-transformer retriever for the transformer comparison.
- `20` tests covering the retrievers and the evaluator.

## Evidence

| Retriever | MRR |
|---|---|
| Word2Vec, first configuration | `0.0232` |
| Word2Vec, best grid configuration | `0.6985` |
| BM25 | `0.6738` |
| MPNet | `0.9297` |

MPNet finished about `38%` above BM25. The first Word2Vec result was a failure, and the parameter search is the record of the fix. Treat the Word2Vec lift as exploratory: the winning configuration was chosen on the same queries it is reported on. The BM25 to MPNet comparison involves no tuning: BM25 runs at its default `k1=1.2`, `b=0.75`, and MPNet is used pretrained, with no parameter selected from these results. The comparison uses MRR; Recall@k is implemented in the evaluator but is not the headline metric.

## What broke

The first Word2Vec configuration scored `0.0232`, which is effectively no retrieval. The useful result is not that transformers won. It is that a static embedding can move from `0.0232` to `0.6985` with the right window, dimension, and training setup, and still lose to a transformer.

## What I'd do next

Add a reranking stage and a per-query error analysis, so the aggregate MRR is backed by examples of what each retriever misses.
