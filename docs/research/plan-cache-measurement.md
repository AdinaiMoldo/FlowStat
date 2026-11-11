# Plan cache (US-41, M9)

Measured on 11 November 2026 with `benchmarks/bench_plan_cache.py` (one core of a cloud container,
Python 3.13, SQLite on a local file, the example workflow of 25 steps, 50 runs stored). The requests
go through the test client of FastAPI in this process, so the figures are the work of the app and
contain no network and no uvicorn. The script was run three times; the table is one run, and the
three agree to within 0.5 ms on every cell but the first (below).

## The question

`docs/research/api-measurement.md` (M6) found that a report costs 8 to 9 ms because `get_plan`
reads the version and compiles it again, and left the stored form of the plan to M8, then M9. The
plan of a published version never changes (its text and the snapshot of its catalogue are frozen,
DR-03), so a cache keyed by those two texts cannot go stale. The decision rule written before the
measurement: keep it only if a report gets at least twice as fast and nothing else gets slower.

## What was built

`compiler/cache.py`: a bounded memo (64 plans, least recently used out first, one lock, an error is
never kept). `WorkflowService.get_plan` asks it for a **published** version with the key
`(workflow, number, text, catalogue snapshot)`; a draft is compiled every time, because its text
changes. The cache belongs to the `Registry`, which is the object of a process that every place that
builds a service already has (the routes, the pool that drives a run, the resume and the expiry), so
a cache is not a global and two apps in one test process do not share one.

## Result

| request | no cache: median ms | cache: median ms | faster |
| --- | --- | --- | --- |
| `GET /runs/{id}` (report) | 13.3 | 4.7 | 2.8x |
| `GET /workflows/{name}/versions/1/graph` | 12.2 | 4.3 | 2.8x |
| `GET /runs` (50 runs) | 20.4 | 19.1 | 1.1x |
| start a run, to the approval | 92.2 | 64.7 | 1.4x |

Over the three runs the report was 2.4x to 2.8x faster and the graph 2.2x to 2.8x; the list was
1.0x to 1.1x (noise); the start of a run was 1.4x each time. After a run the cache had 325 hits, 1
miss and 1 plan.

- The report and the graph cost about 4.5 ms now, which is the floor of a request with a token (1
  ms) plus the reading of the events and the version. The 7 to 8 ms that went are the compile.
- The list was not changed on purpose: since M6 it reads the status from the events and asks for no
  plan. The cost per stored run is the same as before (the table in `api-measurement.md`, about
  0.2 ms per run on a quiet machine; here 20 ms for 50 runs, the machine was busier).
- The start of a run asks for the plan at several points (the check of the inputs, the launch, the
  report), so it gained most in absolute terms: 28 ms.
- The first call fills the cache and costs one compile; the benchmark makes it before it measures.

By the rule above the cache is kept: the report is 2.4x to 2.8x faster and nothing got slower.

## What it does not do

- It is per process. A service that is deployed is another process with its own cache, filled by its
  first request.
- It saves the compile, not the reading of the version (the text is read to make the key). That
  read is the 1 ms or so of the 4.5.
- It does not help the first request after a start, and a version is compiled once for each
  process, not once for each machine.
- Memory: a plan of the example is a few tens of kilobytes; 64 of them are some megabytes at most,
  and the size is an argument of the registry.

## Checked

`tests/unit/service/test_plan_cache.py` (a published version compiles once for any number of asks; a
draft and a text that does not compile are never kept; the plan of the cache equals the plan of a
compile; two caches are two), `tests/unit/api/test_plan_cache.py` (five reports, one compile; the
list compiles at most once; the reports with and without the cache are equal; two versions, two
plans), `tests/unit/compiler/test_cache.py` (the order of eviction, an error is not kept, size 0
keeps nothing, eight threads). Faults put in on purpose: caching a draft, 1 test fails; a key of the
name only, 1 fails; no move to the end on a hit, 1 fails.
