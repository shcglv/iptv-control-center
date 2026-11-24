# ADR-0003 — One container first, worker later

**Status:** proposed — needs the owner's confirmation (open question C-04)

## Context

The approved deployment plan asks for a small footprint on a host with ≈1.75 GB RAM available and
929 MB of swap already in use. A separate worker container costs roughly 120–180 MB RSS and a
second deploy unit, but isolates long jobs from request handling.

## Decision (proposed)

Ship a **single `api` container** that runs the HTTP surface and an in-process scheduler. Split a
`worker` container out only when a measured need appears (job duration competing with API latency,
or memory pressure from concurrent ingest).

## Consequences

- Lower footprint now (~120–180 MB instead of ~300 MB), one resource to deploy and roll back.
- Long jobs share the process: they must be bounded (semaphore, one ingest at a time) and must not
  block the event loop. Heavy CPU work goes to `asyncio.to_thread` or is chunked.
- A redeploy kills in-flight jobs. All jobs must be idempotent and resumable; the job table records
  state so a restart re-queues rather than corrupts.
- The HTTP contract does not change when the worker is split later — the job table and the
  scheduler interface stay the same.
