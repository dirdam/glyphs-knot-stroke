import os
import random
import sys
import time
import unittest

sys.path.insert(0, os.path.dirname(__file__))
import knot  # noqa: E402
import test_knot  # noqa: E402


def square():
    c = lambda a, b: (a, (a[0] + (b[0] - a[0]) / 3, a[1] + (b[1] - a[1]) / 3), (a[0] + 2 * (b[0] - a[0]) / 3, a[1] + 2 * (b[1] - a[1]) / 3), b)
    pts = [(0, 0), (100, 0), (100, 100), (0, 100)]
    return {"segments": [c(pts[i], pts[(i + 1) % 4]) for i in range(4)], "closed": True}


class PerfTests(unittest.TestCase):
    def timed(self, paths, limit=5.0):
        t = time.time()
        cs = knot.find_crossings(paths)
        knot.assign_over_under(paths, cs)
        for c in cs:
            knot.over_piece(paths, c, 40)
        self.assertLess(time.time() - t, limit)
        return cs

    def test_square_straight_lines(self):
        self.assertEqual(len(self.timed([square()])), 0)

    def test_random_closed_path(self):
        random.seed(1)
        pts = [(random.uniform(0, 500), random.uniform(0, 500)) for _ in range(40)]
        self.timed([test_knot.smooth_closed(pts)])

    def test_open_path_with_overlapping_collinear_segments(self):
        seg = ((0, 0), (10, 0), (20, 0), (30, 0))
        back = ((30, 0), (20, 0), (10, 0), (0, 0))
        self.timed([{"segments": [seg, back], "closed": False}], limit=5.0)


if __name__ == "__main__":
    unittest.main()
