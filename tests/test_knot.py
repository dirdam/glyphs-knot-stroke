import math
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "KnotStroke.glyphsFilter", "Contents", "Resources"))
import knot  # noqa: E402


def smooth_closed(points):
    """Closed Catmull-Rom spline through points as cubic segments."""
    n = len(points)
    segs = []
    for i in range(n):
        p0, p1, p2, p3 = points[i - 1], points[i], points[(i + 1) % n], points[(i + 2) % n]
        c1 = (p1[0] + (p2[0] - p0[0]) / 6, p1[1] + (p2[1] - p0[1]) / 6)
        c2 = (p2[0] - (p3[0] - p1[0]) / 6, p2[1] - (p3[1] - p1[1]) / 6)
        segs.append((p1, c1, c2, p2))
    return {"segments": segs, "closed": True}


def circle(n=8, r=100):
    return smooth_closed([(r * math.cos(2 * math.pi * k / n), r * math.sin(2 * math.pi * k / n)) for k in range(n)])


def trefoil(n=36):
    pts = []
    for k in range(n):
        t = 2 * math.pi * k / n
        pts.append((100 * (math.sin(t) + 2 * math.sin(2 * t)) / 3, 100 * (math.cos(t) - 2 * math.cos(2 * t)) / 3))
    return smooth_closed(pts)


def figure_eight(n=24):
    pts = []
    for k in range(n):
        t = 2 * math.pi * k / n
        pts.append((100 * math.sin(t), 60 * math.sin(2 * t)))
    return smooth_closed(pts)


class CrossingTests(unittest.TestCase):
    def test_circle_has_no_crossings(self):
        self.assertEqual(len(knot.find_crossings([circle()])), 0)

    def test_figure_eight_has_one(self):
        self.assertEqual(len(knot.find_crossings([figure_eight()])), 1)

    def test_trefoil_has_three(self):
        self.assertEqual(len(knot.find_crossings([trefoil()])), 3)

    def test_two_overlapping_circles_cross_twice(self):
        a = circle()
        b = {"segments": [tuple((x + 100, y) for x, y in s) for s in a["segments"]], "closed": True}
        self.assertEqual(len(knot.find_crossings([a, b])), 2)

    def test_crossing_point_lies_on_both_strands(self):
        paths = [trefoil()]
        for c in knot.find_crossings(paths):
            for key in ("a", "b"):
                pi, s = c[key]
                seg = paths[pi]["segments"][int(s)]
                p = knot.point_at(seg, s - int(s))
                self.assertAlmostEqual(p[0], c["point"][0], delta=0.1)
                self.assertAlmostEqual(p[1], c["point"][1], delta=0.1)


class OverUnderTests(unittest.TestCase):
    def test_trefoil_alternates_along_path(self):
        paths = [trefoil()]
        cs = knot.assign_over_under(paths, knot.find_crossings(paths))
        visits = []
        for ci, c in enumerate(cs):
            visits.append((c["a"][1], ci, "a"))
            visits.append((c["b"][1], ci, "b"))
        states = [cs[ci]["over"] == side for _, ci, side in sorted(visits)]
        self.assertEqual(len(states), 6)
        self.assertTrue(all(states[i] != states[i + 1] for i in range(5)))
        self.assertNotEqual(states[-1], states[0])  # closed loop keeps alternating

    def test_each_crossing_has_exactly_one_over(self):
        paths = [trefoil()]
        for c in knot.assign_over_under(paths, knot.find_crossings(paths)):
            self.assertIn(c["over"], ("a", "b"))

    def test_flip_inverts(self):
        paths = [trefoil()]
        a = [c["over"] for c in knot.assign_over_under(paths, knot.find_crossings(paths))]
        b = [c["over"] for c in knot.assign_over_under(paths, knot.find_crossings(paths), flip=True)]
        self.assertTrue(all(x != y for x, y in zip(a, b)))


class PieceTests(unittest.TestCase):
    def test_over_piece_length_matches_request(self):
        paths = [trefoil()]
        cs = knot.assign_over_under(paths, knot.find_crossings(paths))
        piece = knot.over_piece(paths, cs[0], 30)
        length = 0.0
        for seg in piece:
            pts = [knot.point_at(seg, k / 50) for k in range(51)]
            length += sum(math.dist(pts[k], pts[k + 1]) for k in range(50))
        self.assertAlmostEqual(length, 60, delta=2)


if __name__ == "__main__":
    unittest.main()
