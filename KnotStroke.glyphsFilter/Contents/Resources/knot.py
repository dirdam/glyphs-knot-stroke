"""Pure-geometry core of Knot Stroke: crossings and over/under assignment.

No Glyphs imports, so it can be tested with plain Python.

A path is a dict {"segments": [seg, ...], "closed": bool}. A segment is four
(x, y) points (cubic Bezier; a line has its control points on the line).
A path parameter ``s`` is ``segment_index + t`` with t in [0, 1].
"""

import math

EPS = 1e-6


def lerp(a, b, t):
    return (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t)


def point_at(seg, t):
    p0, p1, p2, p3 = seg
    a, b, c = lerp(p0, p1, t), lerp(p1, p2, t), lerp(p2, p3, t)
    d, e = lerp(a, b, t), lerp(b, c, t)
    return lerp(d, e, t)


def tangent_at(seg, t):
    p0, p1, p2, p3 = seg
    mt = 1 - t
    dx = 3 * (mt * mt * (p1[0] - p0[0]) + 2 * mt * t * (p2[0] - p1[0]) + t * t * (p3[0] - p2[0]))
    dy = 3 * (mt * mt * (p1[1] - p0[1]) + 2 * mt * t * (p2[1] - p1[1]) + t * t * (p3[1] - p2[1]))
    if abs(dx) < 1e-12 and abs(dy) < 1e-12:  # degenerate handle: fall back to chord
        dx, dy = p3[0] - p0[0], p3[1] - p0[1]
    return (dx, dy)


def split(seg, t):
    p0, p1, p2, p3 = seg
    a, b, c = lerp(p0, p1, t), lerp(p1, p2, t), lerp(p2, p3, t)
    d, e = lerp(a, b, t), lerp(b, c, t)
    f = lerp(d, e, t)
    return (p0, a, d, f), (f, e, c, p3)


def sub_segment(seg, t0, t1):
    """Part of seg between t0 < t1."""
    if t0 > 0:
        seg = split(seg, t0)[1]
        t1 = (t1 - t0) / (1 - t0) if t0 < 1 else 1
    if t1 < 1:
        seg = split(seg, t1)[0]
    return seg


def bbox(seg):
    xs, ys = [p[0] for p in seg], [p[1] for p in seg]
    return min(xs), min(ys), max(xs), max(ys)


def _overlap(a, b, pad=0.0):
    return not (a[2] + pad < b[0] or b[2] + pad < a[0] or a[3] + pad < b[1] or b[3] + pad < a[1])


def _flat(seg, tol=0.05):
    p0, p1, p2, p3 = seg
    dx, dy = p3[0] - p0[0], p3[1] - p0[1]
    d = math.hypot(dx, dy)
    if d < 1e-12:
        return max(math.hypot(p1[0] - p0[0], p1[1] - p0[1]), math.hypot(p2[0] - p0[0], p2[1] - p0[1])) < tol
    return max(abs((p1[0] - p0[0]) * dy - (p1[1] - p0[1]) * dx),
               abs((p2[0] - p0[0]) * dy - (p2[1] - p0[1]) * dx)) / d < tol


def _line_hit(p, q, r, s):
    """Intersection of segments pq and rs -> (u, v) in [0, 1]^2 or None."""
    d1 = (q[0] - p[0], q[1] - p[1])
    d2 = (s[0] - r[0], s[1] - r[1])
    den = d1[0] * d2[1] - d1[1] * d2[0]
    if abs(den) < 1e-12:
        return None
    w = (r[0] - p[0], r[1] - p[1])
    u = (w[0] * d2[1] - w[1] * d2[0]) / den
    v = (w[0] * d1[1] - w[1] * d1[0]) / den
    if -1e-9 <= u <= 1 + 1e-9 and -1e-9 <= v <= 1 + 1e-9:
        return min(max(u, 0.0), 1.0), min(max(v, 0.0), 1.0)
    return None


def intersect_segments(a, b, tol=0.05, depth=0, ta=(0.0, 1.0), tb=(0.0, 1.0)):
    """All (ta, tb) parameter pairs where cubics a and b intersect."""
    if not _overlap(bbox(a), bbox(b)):
        return []
    if depth > 40 or (_flat(a, tol) and _flat(b, tol)):
        hit = _line_hit(a[0], a[3], b[0], b[3])
        if hit is None:
            return []
        return [(ta[0] + (ta[1] - ta[0]) * hit[0], tb[0] + (tb[1] - tb[0]) * hit[1])]
    out = []
    am = (ta[0] + ta[1]) / 2
    bm = (tb[0] + tb[1]) / 2
    a_parts = [(split(a, 0.5)[0], (ta[0], am)), (split(a, 0.5)[1], (am, ta[1]))] if not _flat(a, tol) else [(a, ta)]
    b_parts = [(split(b, 0.5)[0], (tb[0], bm)), (split(b, 0.5)[1], (bm, tb[1]))] if not _flat(b, tol) else [(b, tb)]
    for sa, ra in a_parts:
        for sb, rb in b_parts:
            out += intersect_segments(sa, sb, tol, depth + 1, ra, rb)
    return out


def _refine(a, b, ta, tb):
    """Newton polish of an intersection estimate."""
    for _ in range(6):
        pa, pb = point_at(a, ta), point_at(b, tb)
        fx, fy = pa[0] - pb[0], pa[1] - pb[1]
        da, db = tangent_at(a, ta), tangent_at(b, tb)
        det = -da[0] * db[1] + da[1] * db[0]
        if abs(det) < 1e-12:
            break
        dta = (fx * db[1] - db[0] * fy) / det
        dtb = (da[1] * fx - da[0] * fy) / det
        ta2, tb2 = min(max(ta + dta, 0.0), 1.0), min(max(tb + dtb, 0.0), 1.0)
        if abs(ta2 - ta) + abs(tb2 - tb) < 1e-10:
            ta, tb = ta2, tb2
            break
        ta, tb = ta2, tb2
    return ta, tb


def path_param(i, t):
    return i + t


def _nseg(path):
    return len(path["segments"])


def _adjacent(path_i, i, j):
    n = _nseg(path_i)
    return j == i + 1 or (path_i["closed"] and i == 0 and j == n - 1 and n > 1)


def find_crossings(paths, tol=0.05):
    """Transversal crossings among all paths (including self-crossings).

    Returns a list of dicts {"a": (path, s), "b": (path, s), "point": (x, y)}.
    Joins between consecutive segments of the same path are not crossings.
    """
    found = []
    for pi, P in enumerate(paths):
        for qi in range(pi, len(paths)):
            Q = paths[qi]
            for i, sa in enumerate(P["segments"]):
                for j, sb in enumerate(Q["segments"]):
                    if pi == qi and j <= i:
                        continue
                    for ta, tb in intersect_segments(sa, sb, tol):
                        ta, tb = _refine(sa, sb, ta, tb)
                        if pi == qi and _adjacent(P, i, j):
                            # skip the shared node, keep genuine extra intersections
                            ea = 1.0 if j == i + 1 else 0.0
                            eb = 0.0 if j == i + 1 else 1.0
                            if abs(ta - ea) < 1e-3 and abs(tb - eb) < 1e-3:
                                continue
                        pt = point_at(sa, ta)
                        # drop duplicates (hits that straddle a subdivision boundary)
                        if any(abs(c["point"][0] - pt[0]) < 1e-2 and abs(c["point"][1] - pt[1]) < 1e-2
                               and c["a"][0] == pi and c["b"][0] == qi for c in found):
                            continue
                        found.append({"a": (pi, path_param(i, ta)), "b": (qi, path_param(j, tb)), "point": pt})
    return found


def assign_over_under(paths, crossings, flip=False):
    """Mark which strand of each crossing is on top, alternating along each path.

    Sets crossing["over"] to "a" or "b". Walking a path, successive crossing
    visits alternate over/under; a crossing already decided from its other
    strand keeps that decision (always consistent for a closed planar curve).
    """
    visits = {pi: [] for pi in range(len(paths))}
    for ci, c in enumerate(crossings):
        visits[c["a"][0]].append((c["a"][1], ci, "a"))
        visits[c["b"][0]].append((c["b"][1], ci, "b"))
    for c in crossings:
        c["over"] = None
    for pi in range(len(paths)):
        k = 1 if flip else 0
        for _, ci, side in sorted(visits[pi]):
            c = crossings[ci]
            if c["over"] is None:
                is_over = (k % 2 == 0)
                c["over"] = side if is_over else ("b" if side == "a" else "a")
            k += 1
    return crossings


def path_tangent(path, s):
    segs = path["segments"]
    i = min(int(math.floor(s)), len(segs) - 1)
    return tangent_at(segs[i], s - i)


def crossing_sine(path_a, sa, path_b, sb):
    ta, tb = path_tangent(path_a, sa), path_tangent(path_b, sb)
    la, lb = math.hypot(*ta), math.hypot(*tb)
    if la < 1e-12 or lb < 1e-12:
        return 1.0
    return abs(ta[0] * tb[1] - ta[1] * tb[0]) / (la * lb)


def walk_length(path, s, dist, direction, step=0.01):
    """Parameter reached after walking ``dist`` of arc length from s (clamped to open ends)."""
    segs = path["segments"]
    n = len(segs)
    walked, cur = 0.0, s
    prev = point_at(segs[min(int(cur), n - 1)], cur - min(int(cur), n - 1))
    for _ in range(int(n / step) + 1):
        nxt = cur + direction * step
        if path["closed"]:
            nxt %= n
        elif nxt < 0 or nxt > n:
            return min(max(nxt, 0.0), float(n))
        i = min(int(nxt), n - 1)
        p = point_at(segs[i], nxt - i)
        walked += math.hypot(p[0] - prev[0], p[1] - prev[1])
        prev, cur = p, nxt
        if walked >= dist:
            break
    return cur


def over_piece(paths, crossing, half_extent):
    """Centerline sub-path (list of segments) of the over strand around its crossing."""
    return strand_piece(paths, crossing, crossing["over"], half_extent)


def under_piece(paths, crossing, half_extent):
    """Same as over_piece, for the strand that goes under."""
    return strand_piece(paths, crossing, "b" if crossing["over"] == "a" else "a", half_extent)


def strand_piece(paths, crossing, side, half_extent):
    pi, s = crossing[side]
    path = paths[pi]
    n = len(path["segments"])
    lo = walk_length(path, s, half_extent, -1)
    hi = walk_length(path, s, half_extent, +1)
    # walk in forward direction from lo to hi, wrapping on closed paths
    if path["closed"] and lo > s:
        lo -= n
    if path["closed"] and hi < s:
        hi += n
    return slice_path(path, lo, hi)


def slice_path(path, s0, s1):
    segs, n = path["segments"], len(path["segments"])
    out = []
    i = int(math.floor(s0))
    while i < s1 and len(out) <= n + 1:
        base = i
        t0 = max(s0 - base, 0.0)
        t1 = min(s1 - base, 1.0)
        if t1 - t0 > 1e-9:
            out.append(sub_segment(segs[i % n], t0, t1))
        i += 1
    return out
