"""Tests for the critical-path extractor.

The strategy is to build small, hand-crafted span trees whose critical path
is unambiguous by construction, plus a handful of malformed inputs that must
be rejected.  All timestamps are plain integers; nothing here depends on a
real clock.
"""

import unittest

from span_critical_path_extractor import Span, extract_critical_path


class TestExtractCriticalPath(unittest.TestCase):

    def test_single_span(self):
        """A root with no children is its own one-element critical path."""
        root = Span("root", None, 0, 10)
        path = extract_critical_path([root])
        self.assertEqual([s.span_id for s in path], ["root"])

    def test_linear_chain(self):
        """a -> b -> c: the whole chain is the critical path."""
        a = Span("a", None, 0, 100)
        b = Span("b", "a", 10, 90)
        c = Span("c", "b", 90, 95)
        path = extract_critical_path([a, b, c])
        self.assertEqual([s.span_id for s in path], ["a", "b", "c"])

    def test_picks_latest_ending_child(self):
        """Between two children, the one ending later wins.

        Here ``fast`` ends at 50 and ``slow`` ends at 80, so ``slow`` is on
        the critical path even though ``fast`` started later.  This encodes
        the documented rule: we rank children by end time, not by duration
        or by start time.
        """
        root = Span("root", None, 0, 100)
        fast = Span("fast", "root", 60, 65)
        slow = Span("slow", "root", 10, 80)
        slow_child = Span("slow_child", "slow", 80, 90)
        path = extract_critical_path([root, fast, slow, slow_child])
        self.assertEqual([s.span_id for s in path], ["root", "slow", "slow_child"])

    def test_starts_after_parent_ends(self):
        """A child starting after the parent ends still follows the end rule.

        Clock skew of this shape is common in real traces.  We do not reject
        it; we simply apply the same latest-end rule as everywhere else.
        """
        root = Span("root", None, 0, 50)
        late = Span("late", "root", 60, 80)
        early = Span("early", "root", 10, 40)
        path = extract_critical_path([root, late, early])
        self.assertEqual([s.span_id for s in path], ["root", "late"])

    def test_child_ends_before_starts(self):
        """A child with end < start is not rejected; skew is tolerated."""
        root = Span("root", None, 0, 100)
        skewed = Span("skewed", "root", 80, 70)
        other = Span("other", "root", 10, 60)
        path = extract_critical_path([root, skewed, other])
        self.assertEqual([s.span_id for s in path], ["root", "skewed"])

    def test_concurrent_sibling_not_in_path(self):
        """A child that ends earlier than its sibling must not appear."""
        root = Span("root", None, 0, 100)
        winner = Span("winner", "root", 0, 100)
        loser = Span("loser", "root", 0, 50)
        path = extract_critical_path([root, winner, loser])
        self.assertEqual([s.span_id for s in path], ["root", "winner"])

    def test_descends_into_winner_only(self):
        """Children of the losing sibling are never visited."""
        root = Span("root", None, 0, 100)
        winner = Span("winner", "root", 0, 90)
        winner_kid = Span("winner_kid", "winner", 90, 95)
        loser = Span("loser", "root", 0, 50)
        loser_kid = Span("loser_kid", "loser", 50, 60)
        path = extract_critical_path([root, winner, winner_kid, loser, loser_kid])
        self.assertEqual(
            [s.span_id for s in path], ["root", "winner", "winner_kid"]
        )

    def test_tie_breaks_by_span_id(self):
        """Two children ending at the same time: higher id wins, deterministically."""
        root = Span("root", None, 0, 100)
        zed = Span("zed", "root", 10, 80)
        alpha = Span("alpha", "root", 20, 80)
        path = extract_critical_path([root, zed, alpha])
        self.assertEqual([s.span_id for s in path], ["root", "zed"])

    def test_tie_breaks_by_span_id_descending_visible(self):
        """When ids sort the other way, the higher id still wins."""
        root = Span("root", None, 0, 100)
        a = Span("a", "root", 10, 80)
        b = Span("b", "root", 20, 80)
        path = extract_critical_path([root, a, b])
        self.assertEqual([s.span_id for s in path], ["root", "b"])

    def test_empty_input_rejected(self):
        with self.assertRaises(ValueError):
            extract_critical_path([])

    def test_duplicate_span_ids_rejected(self):
        root = Span("a", None, 0, 10)
        dup = Span("a", None, 0, 10)
        with self.assertRaises(ValueError):
            extract_critical_path([root, dup])

    def test_dangling_parent_rejected(self):
        """A child whose parent_id is not in the set is a corrupt trace."""
        orphan = Span("orphan", "ghost", 0, 10)
        root = Span("root", None, 0, 10)
        with self.assertRaises(ValueError):
            extract_critical_path([root, orphan])

    def test_multiple_roots_rejected(self):
        a = Span("a", None, 0, 10)
        b = Span("b", None, 0, 10)
        with self.assertRaises(ValueError):
            extract_critical_path([a, b])

    def test_cycle_rejected(self):
        """A self-referential parent edge forms a cycle and must be caught."""
        a = Span("a", "b", 0, 10)
        b = Span("b", "a", 0, 10)
        with self.assertRaises(ValueError):
            extract_critical_path([a, b])


if __name__ == "__main__":
    unittest.main()
