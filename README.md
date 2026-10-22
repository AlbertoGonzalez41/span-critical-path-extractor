# span_critical_path_extractor

Given a completed trace span tree, identify the longest sequential chain of spans that drives the overall latency of the trace. Concurrency is the enemy of the critical path: when children run in parallel, only the one that finishes last contributes.

```python
from span_critical_path_extractor import Span, extract_critical_path

spans = [
    Span("root", None, 0, 100),
    Span("db", "root", 10, 80),
    Span("cache", "root", 10, 30),
    Span("db_parse", "db", 80, 90),
]

path = extract_critical_path(spans)
print([s.span_id for s in path])  # ['root', 'db', 'db_parse']
```

The library exports exactly two names: `Span` (an immutable dataclass with `span_id`, `parent_id`, `start`, `end`) and `extract_critical_path(spans) -> list[Span]`.

## Why this exists

When a trace is slow, the first question is usually "which child dominated the parent's latency?" This library answers that by walking the span tree from the root and, at every level, selecting the single child whose `end` is latest. That child becomes the next link. Everything else at that level is treated as concurrent or overlapping work and dropped from the path.

This is a deliberate, single interpretation. An alternative formulation would compute exclusive durations (the portion of a parent not covered by any child) and maximise the sum along a path. That is a richer model but a different question, and trying to honour both at once produces inconsistent results. We picked the "latest-ending child" rule because it mirrors what most tracing tools render as the critical path of a root span.

## The awkward edge you will hit

Clock skew is not treated as an error. A child that starts after its parent ends, or that ends before it starts, is processed with the same latest-end rule as any other span. Real distributed traces routinely contain skewed timestamps, and rejecting them would make the library unusable on the data it is meant to analyse. If your input is clean and you would prefer skew to be rejected, validate it before calling this function.

Timestamps are opaque numbers — microseconds, milliseconds, epoch seconds, anything totally ordered. The library never compares them to a wall clock, so units are the caller's concern. Keep them consistent within a single call.

Ties (two children ending at the same time) are broken by `span_id` so results are deterministic across runs and platforms.

## Performance

The window keeps a bounded buffer, so `push` is constant time and memory does not
grow with the length of the stream. `peak` and `trough` are linear in the window
size, which is the trade that keeps `push` cheap.

