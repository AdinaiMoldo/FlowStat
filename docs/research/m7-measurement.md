# M7 measurement: events shipped to the core, and the graph

Script: `benchmarks/bench_m7.py` (`python benchmarks/bench_m7.py`). Machine: the development
sandbox (one Linux container, SQLite with `PRAGMA synchronous = FULL`). Limits, as in
`api-measurement.md`: a request goes through the test client of FastAPI in this process, so the
figures are the work of the app and not of a network; the drawing is real Chromium (Playwright) with
the vendored Cytoscape and dagre.

| What | Runs | Median (ms) | p95 (ms) |
| --- | --- | --- | --- |
| a batch that makes a new run, 1 event | 30 | 4.07 | 4.40 |
| a batch that makes a new run, 50 events | 30 | 4.63 | 4.91 |
| a batch that makes a new run, 200 events | 30 | 5.85 | 7.04 |
| the same 200 events delivered again | 30 | 5.20 | 6.76 |
| one round of the outbox, 1000 events in 5 runs (5 batches of 200) | 1 | 38 | - |
| graph function, 9 regions (22 nodes) | 30 | 0.05 | 0.08 |
| graph function, 100 regions (204 nodes) | 30 | 0.40 | 0.42 |
| drawing, 9 regions (click on "Show graph" to the nodes on the screen) | 5 | 149 | 152 |
| drawing, 100 regions (two collapsed groups) | 5 | 130 | 132 |

## Conclusions

- **The cost of a batch is the transaction, not the events.** One event costs 4.1 ms and two hundred
  cost 5.9 ms: about 4 ms is the ingest key, a connection and the commit that SQLite makes durable,
  and each event adds about 9 microseconds. So the batch limit of 200 is not a performance limit; it
  keeps one request small. Sending events one by one would be the slow way (4 ms each), and the
  outbox sends a whole run's pending events at once.
- **A duplicate batch costs about the same as a new one** (5.2 ms against 5.9): the core reads each
  stored event and compares it. That is the price of the rule "identical is ignored, different is a
  conflict", and it is the check that makes at-least-once safe, so it was kept. Nothing is written
  for a duplicate.
- **Shipping is not the bottleneck of a run.** The example run, with its approval, writes 47 events (one batch); the
  1000 events of five runs go in 38 ms. A run takes longer than its own shipping, so the core
  sees a run a second or so after it ends (the idle wait of the thread), not later. This was measured
  in-process; over a network the round trip of each batch is added, which is why a round sends one
  batch per run and not one per event.
- **The graph is cheap on the server and the drawing is cheap in the browser.** Building the graph
  of 100 regions takes 0.4 ms; drawing it takes 130 ms because the fan-out starts collapsed: two
  nodes instead of 204. The 9 regions are 149 ms because all 22 nodes are drawn and laid out (dagre).
  Nothing here needs a cache. If the groups are opened, 100 instances are laid out in the page
  (the browser test waits for it and asserts less than 5 s; it was not timed apart).
- **Not measured:** shipping over a real network, with a core that is busy with its own runs (the
  database is one SQLite file with one writer, so a service and a run of the core wait for each
  other for the length of a commit), and many services at once. The single-writer limit is the one
  that matters and it is stated in `api-spec.md` section 8.
