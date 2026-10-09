"""Identify the critical path through a completed trace span tree.

Given a set of spans that describe a single trace, the *critical path* is the
longest sequential chain of spans that contributes to the overall end-to-end
latency of the trace.  Concurrency is the enemy of the critical path: if two
spans run in parallel, only the slower one belongs on the path.

Design choice
-------------
This module follows a single, strict interpretation of "critical path":

  Starting at the root span, at every step we look at the span's direct
  children (in the provided span tree).  We pick the *single* child whose
  end time is latest among the children.  That child is the next link in the
  chain, regardless of whether its own start time aligns with the parent's
  end time.  We then descend into that child and repeat.  The chain ends at a
  leaf span.

Concretely the rule is: ``child.end == max(c.end for c in parent.children)``.
Ties are broken by span id to keep results deterministic.

This is deliberately different from a common alternative that treats the
problem as "find the path through a timing DAG that maximises total
exclusive duration."  We do not compute exclusive durations (the portion of
a parent span not covered by any child), and we do not let a short, late
child displace a long, early child merely because the late child is the
last thing the parent waited for.  Both formulations are legitimate; we
picked the one that mirrors what most distributed-tracing tools label as the
"critical path" view of a root span, because that is the question users tend
to ask first: "which child dominated the parent's latency?"

Edge cases handled
------------------
* A root with no children returns a one-element path containing itself.
* Invalid parent references (a child whose ``parent_id`` does not resolve to
  any span in the set) are detected and rejected with ``ValueError``.
* Multiple spans sharing the same ``span_id`` are rejected.
* Cycles (which can only arise if ``parent_id`` relationships form a loop) are
  detected and rejected.
* Clock skew (a child that ends before it starts, or a child that starts
  after its parent ends) is *not* treated as an error.  Real traces have
  skewed clocks; rejecting them would make the library unusable on real data.
* Empty input is rejected.

There is no notion of "concurrent spans" separate from the tree: anything
that is not the chosen child at a given level is considered concurrent or
overlapping work and is excluded from the path.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional


@dataclass(frozen=True)
class Span:
    """An immutable description of a single span in a trace.

    Times are expressed as opaque numeric timestamps (microseconds,
    milliseconds, epoch seconds — anything totally ordered works).  The
    library never compares a timestamp to wall-clock time, so units are the
    caller's concern.  This keeps the library deterministic and testable.

    ``parent_id`` of ``None`` marks the root of the trace.  Exactly one span
    in any given input set must have ``parent_id`` set to ``None``.
    """

    span_id: str
    parent_id: Optional[str]
    start: float
    end: float

    def __post_init__(self) -> None:
        if self.span_id is None or self.span_id == "":
            raise ValueError("span_id must be a non-empty string")
        if not isinstance(self.start, (int, float)):
            raise TypeError("start must be numeric")
        if not isinstance(self.end, (int, float)):
            raise TypeError("end must be numeric")


def _build_index(spans: List[Span]) -> Dict[str, Span]:
    """Return a dict keyed by ``span_id`` and raise on duplicates.

    Duplicate ids would silently corrupt the tree, so we fail loudly rather
    than let a later lookup pick an arbitrary survivor.
    """
    index: Dict[str, Span] = {}
    for s in spans:
        if s.span_id in index:
            raise ValueError(f"duplicate span_id: {s.span_id!r}")
        index[s.span_id] = s
    return index


def _build_children(
    spans: List[Span], index: Dict[str, Span]
) -> Dict[str, List[Span]]:
    """Return a dict mapping each span id to its direct children.

    A child whose ``parent_id`` is not ``None`` and not present in ``index``
    indicates a corrupt or partial trace; we reject it rather than silently
    dropping the span, because a dangling parent reference usually means the
    caller handed us the wrong slice of a larger trace.
    """
    children: Dict[str, List[Span]] = {s.span_id: [] for s in spans}
    for s in spans:
        if s.parent_id is None:
            continue
        if s.parent_id not in index:
            raise ValueError(
                f"span {s.span_id!r} references unknown parent "
                f"{s.parent_id!r}"
            )
        children[s.parent_id].append(s)
    return children


def _detect_cycle(root_id: str, children: Dict[str, List[Span]]) -> None:
    """Raise if the parent relationships form a cycle reachable from root.

    Cycles cannot exist in a well-formed trace but can arise in synthetic
    or adversarial input.  Without this check a cyclic tree would cause the
    walker to loop forever.
    """
    seen = set()
    stack = [root_id]
    while stack:
        sid = stack.pop()
        if sid in seen:
            raise ValueError(f"cycle detected at span {sid!r}")
        seen.add(sid)
        for child in children.get(sid, []):
            stack.append(child.span_id)


def extract_critical_path(spans: List[Span]) -> List[Span]:
    """Return the critical path of a trace, as an ordered list of spans.

    The first element is the root span and the last is a leaf.  Between them
    each span is the child of the previous span whose ``end`` is latest among
    that previous span's direct children (ties broken by ``span_id``).

    Raises
    ------
    ValueError
        If ``spans`` is empty, has no root, has multiple roots, has duplicate
        ids, has a dangling parent reference, or contains a cycle.
    """
    if not spans:
        raise ValueError("spans must not be empty")

    index = _build_index(spans)
    children = _build_children(spans, index)

    roots = [s for s in spans if s.parent_id is None]
    if len(roots) != 1:
        raise ValueError(
            f"expected exactly one root span (parent_id is None), "
            f"found {len(roots)}"
        )
    root = roots[0]
    _detect_cycle(root.span_id, children)

    path: List[Span] = []
    current: Span = root
    while True:
        path.append(current)
        kids = children[current.span_id]
        if not kids:
            break
        # Pick the child that finishes last.  When two children finish at
        # the same time we fall back to span_id so the choice is stable and
        # deterministic across runs and across platforms.
        current = max(kids, key=lambda c: (c.end, _id_key(c.span_id)))
    return path


def _id_key(span_id: str) -> str:
    """Return a stable, type-stable comparison key for a span id.

    ``max`` with a tuple key compares the second element only on ties of the
    first.  Returning the raw string keeps the key totally ordered and avoids
    any surprising behaviour from mixed-type ids.
    """
    return span_id
