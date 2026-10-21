# encoding: utf-8
"""Glyphs <-> geometry glue: read paths, stroke them with Offset Curve, boolean with Core Graphics (macOS 13+).

A "shape" here is a CGPath of closed subpaths filled with the non-zero rule."""

from __future__ import division, print_function, unicode_literals
import objc
from AppKit import NSPoint, NSClassFromString
from Quartz import (CGPathCreateMutable, CGPathMoveToPoint, CGPathAddLineToPoint, CGPathAddCurveToPoint,
                    CGPathCloseSubpath, CGPathApply, CGPathIsEmpty, CGPathGetBoundingBox,
                    CGPathCreateCopyByUnioningPath, CGPathCreateCopyBySubtractingPath,
                    CGPathCreateCopyByIntersectingPath, CGPathCreateCopyByNormalizing)
from GlyphsApp import GSLayer, GSPath, GSNode, LINE, CURVE, OFFCURVE

import math

TINY = 1.5  # shorter than this, a segment is a leftover step of a cut, not outline

OFFSET_CURVE = NSClassFromString("GlyphsFilterOffsetCurve")


def selected_paths(layer):
    """Paths with at least one selected node, or all paths when none is selected (e.g. on export)."""
    paths = [layer.paths[i] for i in range(len(layer.paths))]
    selection = [layer.selection[i] for i in range(len(layer.selection))]
    picked = [p for p in paths if any(p.nodes[i] in selection for i in range(len(p.nodes)))]
    return picked or paths


def read_paths(paths):
    """Glyphs paths -> knot.py path dicts. Raises ValueError on quadratic curves."""
    out = []
    for path in paths:
        # Index loops only: Glyphs' segment objects wrap around when iterated with `for`, which never ends.
        nodes = [path.nodes[i] for i in range(len(path.nodes))]
        starts = [i for i, n in enumerate(nodes) if n.type != OFFCURVE]
        if len(starts) < 2 and not (path.closed and starts):
            continue
        k = starts[0]
        closed = bool(path.closed)
        seq = nodes[k:] + nodes[:k] + ([nodes[k]] if closed else [])
        segs = []
        prev = (seq[0].position.x, seq[0].position.y)
        pending = []
        for n in seq[1:]:
            pt = (n.position.x, n.position.y)
            if n.type == OFFCURVE:
                pending.append(pt)
                continue
            if not pending:
                c1 = (prev[0] + (pt[0] - prev[0]) / 3, prev[1] + (pt[1] - prev[1]) / 3)
                c2 = (prev[0] + 2 * (pt[0] - prev[0]) / 3, prev[1] + 2 * (pt[1] - prev[1]) / 3)
            elif len(pending) == 2:
                c1, c2 = pending
            else:
                raise ValueError("Knot Stroke only supports straight and cubic segments")
            segs.append((prev, c1, c2, pt))
            prev, pending = pt, []
        if segs:
            out.append({"segments": segs, "closed": closed})
    return out


def _is_line(seg):
    p0, p1, p2, p3 = seg
    def on_line(p):
        return abs((p[0] - p0[0]) * (p3[1] - p0[1]) - (p[1] - p0[1]) * (p3[0] - p0[0])) < 1e-6
    return on_line(p1) and on_line(p2)


def build_gspath(segments, closed):
    """Segments -> GSPath. Closed paths end on their start point (Glyphs convention)."""
    path = GSPath()
    nodes = []
    if not closed:
        nodes.append(GSNode(NSPoint(*segments[0][0]), LINE))
    for seg in segments:
        if _is_line(seg):
            nodes.append(GSNode(NSPoint(*seg[3]), LINE))
        else:
            nodes.append(GSNode(NSPoint(*seg[1]), OFFCURVE))
            nodes.append(GSNode(NSPoint(*seg[2]), OFFCURVE))
            nodes.append(GSNode(NSPoint(*seg[3]), CURVE))
    path.nodes = nodes
    path.closed = closed
    return path


def stroke_paths(path_dicts, width_x, width_y, cap=0):
    """Stroke centerlines with the built-in Offset Curve engine; return a shape."""
    tmp = GSLayer()
    for p in path_dicts:
        if p["segments"]:
            tmp.shapes.append(build_gspath(p["segments"], p["closed"]))
    # Glyphs 3 signature (see OffsetCurve.glyphsFilter/Contents/Headers); cap is a GSLineCapStyle (0 = butt).
    OFFSET_CURVE.offsetLayer_offsetX_offsetY_makeStroke_autoStroke_position_metrics_error_shadow_capStyleStart_capStyleEnd_keepCompatibleOutlines_(
        tmp, width_x / 2.0, width_y / 2.0, True, False, 0.5, None, None, None, cap, cap, False)
    return layer_to_shape(tmp)


def stroke_segments(segments, closed, width_x, width_y, cap=0):
    return stroke_paths([{"segments": segments, "closed": closed}], width_x, width_y, cap)


def layer_to_shape(layer):
    """Closed paths of a layer -> shape."""
    shape = CGPathCreateMutable()
    for path in layer.paths:
        nodes = [path.nodes[i] for i in range(len(path.nodes))]
        if len(nodes) < 3 or not path.closed:
            continue
        CGPathMoveToPoint(shape, None, nodes[-1].position.x, nodes[-1].position.y)
        pending = []
        for n in nodes:
            pt = (n.position.x, n.position.y)
            if n.type == OFFCURVE:
                pending.append(pt)
            elif n.type == CURVE and len(pending) == 2:
                CGPathAddCurveToPoint(shape, None, pending[0][0], pending[0][1], pending[1][0], pending[1][1], pt[0], pt[1])
                pending = []
            else:
                CGPathAddLineToPoint(shape, None, pt[0], pt[1])
                pending = []
        CGPathCloseSubpath(shape)
    return CGPathCreateCopyByNormalizing(shape, False)


def union(shapes):
    result = CGPathCreateMutable()
    for shape in shapes:
        result = CGPathCreateCopyByUnioningPath(result, shape, False)
    return CGPathCreateCopyByNormalizing(result, False)


def difference(a, b):
    if CGPathIsEmpty(a) or CGPathIsEmpty(b):
        return a
    return CGPathCreateCopyBySubtractingPath(a, b, False)


def intersection(a, b):
    if CGPathIsEmpty(a) or CGPathIsEmpty(b):
        return CGPathCreateMutable()
    return CGPathCreateCopyByIntersectingPath(a, b, False)


def subpaths(shape):
    """Shape -> [(start point, [(c1, c2, end)])], one per closed subpath; c1 and c2 are None for a line."""
    out = []
    state = {"start": None, "cur": None, "segs": None}

    def finish():
        if state["segs"] and state["cur"] != state["start"]:
            state["segs"].append((None, None, state["start"]))  # the implicit closing line
        if state["segs"]:
            out.append((state["start"], state["segs"]))
        state["segs"] = None

    def visit(info, element):
        kind, p = element.type, element.points
        if kind == 0:
            finish()
            state["start"] = state["cur"] = (p[0].x, p[0].y)
            state["segs"] = []
        elif state["segs"] is None:
            return
        elif kind == 1:
            state["cur"] = (p[0].x, p[0].y)
            state["segs"].append((None, None, state["cur"]))
        elif kind == 2:  # quadratic -> cubic against the current point
            cur, c, e = state["cur"], (p[0].x, p[0].y), (p[1].x, p[1].y)
            state["segs"].append(((cur[0] + 2 * (c[0] - cur[0]) / 3, cur[1] + 2 * (c[1] - cur[1]) / 3),
                                  (e[0] + 2 * (c[0] - e[0]) / 3, e[1] + 2 * (c[1] - e[1]) / 3), e))
            state["cur"] = e
        elif kind == 3:
            state["cur"] = (p[2].x, p[2].y)
            state["segs"].append(((p[0].x, p[0].y), (p[1].x, p[1].y), state["cur"]))
        elif kind == 4:
            finish()

    CGPathApply(shape, None, visit)
    finish()
    return out


def describe(shape):
    """Short text for the log: subpath count and bounds."""
    box = CGPathGetBoundingBox(shape)
    return "%d subpaths%s" % (len(subpaths(shape)), "" if CGPathIsEmpty(shape) else
                              " [%.0f,%.0f %.0fx%.0f]" % (box.origin.x, box.origin.y, box.size.width, box.size.height))


def drop_tiny_segments(start, segs, tol):
    """Remove segments (straight or curved) that stay within tol of their start, pulling the previous end onto them.

    The cuts leave steps a fraction of a unit long next to the over strand; without this each one
    costs a doubled node.
    """
    segs = list(segs)
    i = 0
    while i < len(segs) and len(segs) > 3:
        c1, c2, end = segs[i]
        begin = segs[i - 1][2]
        pts = [end] + ([c1, c2] if c1 is not None else [])
        if all(math.hypot(p[0] - begin[0], p[1] - begin[1]) < tol for p in pts):
            pc1, pc2, _ = segs[i - 1]
            dx, dy = end[0] - begin[0], end[1] - begin[1]
            segs[i - 1] = (pc1, None if pc2 is None else (pc2[0] + dx, pc2[1] + dy), end)
            del segs[i]
            i = max(i - 1, 0)
            continue
        i += 1
    # a start point that was removed has been replaced by the previous segment's end
    return segs[-1][2], segs


def _bez(p0, p1, p2, p3, u):
    m = 1 - u
    a, b, c, d = m * m * m, 3 * m * m * u, 3 * m * u * u, u * u * u
    return (a * p0[0] + b * p1[0] + c * p2[0] + d * p3[0], a * p0[1] + b * p1[1] + c * p2[1] + d * p3[1])


def _dist_to_polyline(p, poly):
    best = float("inf")
    for a, b in zip(poly, poly[1:]):
        dx, dy = b[0] - a[0], b[1] - a[1]
        l2 = dx * dx + dy * dy
        t = 0.0 if l2 == 0 else max(0.0, min(1.0, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / l2))
        best = min(best, math.hypot(p[0] - a[0] - t * dx, p[1] - a[1] - t * dy))
    return best


def _has_interior_extremum(p0, p1, p2, p3):
    for k in (0, 1):
        # derivative of a cubic is a quadratic in u
        a = 3 * (-p0[k] + 3 * p1[k] - 3 * p2[k] + p3[k])
        b = 6 * (p0[k] - 2 * p1[k] + p2[k])
        c = 3 * (p1[k] - p0[k])
        roots = []
        if abs(a) < 1e-9:
            if abs(b) > 1e-9:
                roots = [-c / b]
        else:
            disc = b * b - 4 * a * c
            if disc >= 0:
                r = math.sqrt(disc)
                roots = [(-b + r) / (2 * a), (-b - r) / (2 * a)]
        if any(0.01 < u < 0.99 for u in roots):
            return True
    return False


def merge_two_curves(a, a_begin, b, tol):
    """One cubic standing in for consecutive cubics a and b, or None when it would not match within tol."""
    p0, p3 = a_begin, b[2]
    t0 = (a[0][0] - p0[0], a[0][1] - p0[1])
    t3 = (p3[0] - b[1][0], p3[1] - b[1][1])
    l0, l3 = math.hypot(*t0), math.hypot(*t3)
    if l0 < 1e-9 or l3 < 1e-9:
        return None
    t0, t3 = (t0[0] / l0, t0[1] / l0), (t3[0] / l3, t3[1] / l3)
    n = 12
    samples = [_bez(a_begin, a[0], a[1], a[2], k / n) for k in range(n)]
    samples += [_bez(a[2], b[0], b[1], b[2], k / n) for k in range(n + 1)]
    acc, us = 0.0, [0.0]
    for q, r in zip(samples, samples[1:]):
        acc += math.hypot(r[0] - q[0], r[1] - q[1])
        us.append(acc)
    if acc < 1e-9:
        return None
    us = [u / acc for u in us]
    c00 = c01 = c11 = x0 = x1 = 0.0
    for d, u in zip(samples, us):
        m = 1 - u
        b0, b1, b2, b3 = m * m * m, 3 * m * m * u, 3 * m * u * u, u * u * u
        a1 = (t0[0] * b1, t0[1] * b1)
        a2 = (-t3[0] * b2, -t3[1] * b2)
        tmp = (d[0] - (p0[0] * (b0 + b1) + p3[0] * (b2 + b3)), d[1] - (p0[1] * (b0 + b1) + p3[1] * (b2 + b3)))
        c00 += a1[0] * a1[0] + a1[1] * a1[1]
        c01 += a1[0] * a2[0] + a1[1] * a2[1]
        c11 += a2[0] * a2[0] + a2[1] * a2[1]
        x0 += a1[0] * tmp[0] + a1[1] * tmp[1]
        x1 += a2[0] * tmp[0] + a2[1] * tmp[1]
    det = c00 * c11 - c01 * c01
    if abs(det) < 1e-12:
        return None
    alpha, beta = (x0 * c11 - x1 * c01) / det, (c00 * x1 - c01 * x0) / det
    if alpha <= 0 or beta <= 0:
        return None
    p1 = (p0[0] + t0[0] * alpha, p0[1] + t0[1] * alpha)
    p2 = (p3[0] - t3[0] * beta, p3[1] - t3[1] * beta)
    if _has_interior_extremum(p0, p1, p2, p3):
        return None
    fitted = [_bez(p0, p1, p2, p3, k / 40.0) for k in range(41)]
    if any(_dist_to_polyline(q, fitted) > tol for q in samples):
        return None
    original = [_bez(a_begin, a[0], a[1], a[2], k / 20.0) for k in range(21)] + \
               [_bez(a[2], b[0], b[1], b[2], k / 20.0) for k in range(21)]
    if any(_dist_to_polyline(q, original) > tol for q in fitted):
        return None
    return (p1, p2, p3)


def merge_smooth_curves(start, segs, tol=0.25, max_angle=math.radians(4)):
    """Join consecutive curves that are one smooth curve cut in pieces.

    The offset engine and the cuts split edges into several pieces; merging them leaves nodes only
    where the outline really needs them. Nodes on a horizontal or vertical tangent (extremes) stay.
    """
    segs = list(segs)
    i = 0
    while i < len(segs) and len(segs) > 3:
        a, b = segs[i - 1], segs[i]
        if a[1] is not None and b[0] is not None and i > 0:
            end = a[2]
            d1 = (end[0] - a[1][0], end[1] - a[1][1])
            d2 = (b[0][0] - end[0], b[0][1] - end[1])
            l1, l2 = math.hypot(*d1), math.hypot(*d2)
            if l1 > 1e-9 and l2 > 1e-9:
                dot = (d1[0] * d2[0] + d1[1] * d2[1]) / (l1 * l2)
                cross = (d1[0] * d2[1] - d1[1] * d2[0]) / (l1 * l2)
                axis = min(abs(d1[0]), abs(d1[1])) / l1 < math.sin(math.radians(1))
                if dot > 0 and abs(math.atan2(cross, dot)) < max_angle and not axis:
                    begin = segs[i - 2][2]
                    merged = merge_two_curves(a, begin, b, tol)
                    if merged:
                        segs[i - 1:i + 1] = [merged]
                        i = max(i - 1, 1)
                        continue
        i += 1
    return segs[-1][2], segs


def smooth_junctions(segs, max_angle=math.radians(4)):
    """Align the handles at near-tangent curve-curve joins; return the indices of the segments whose end is smooth."""
    smooth = []
    n = len(segs)
    for j in range(n):
        a, b = segs[j], segs[(j + 1) % n]
        if a[1] is None or b[0] is None:
            continue
        end = a[2]
        d1 = (end[0] - a[1][0], end[1] - a[1][1])
        d2 = (b[0][0] - end[0], b[0][1] - end[1])
        l1, l2 = math.hypot(*d1), math.hypot(*d2)
        if l1 < 1e-9 or l2 < 1e-9:
            continue
        cross = (d1[0] * d2[1] - d1[1] * d2[0]) / (l1 * l2)
        dot = (d1[0] * d2[0] + d1[1] * d2[1]) / (l1 * l2)
        if dot <= 0 or abs(math.atan2(cross, dot)) > max_angle:
            continue
        ux, uy = d1[0] / l1 + d2[0] / l2, d1[1] / l1 + d2[1] / l2
        norm = math.hypot(ux, uy)
        ux, uy = ux / norm, uy / norm
        segs[j] = (a[0], (end[0] - ux * l1, end[1] - uy * l1), end)
        segs[(j + 1) % n] = ((end[0] + ux * l2, end[1] + uy * l2), b[1], b[2])
        smooth.append(j)
    return smooth


def shape_to_layer(result, layer, replaced):
    """Replace the `replaced` paths of the layer with the paths of a shape; other paths stay."""
    new_paths = []
    for start, segs in subpaths(result):
        if len(segs) < 2:
            continue
        start, segs = drop_tiny_segments(start, segs, TINY)
        start, segs = merge_smooth_curves(start, segs)
        smooth = smooth_junctions(segs)
        nodes, ends = [], []
        for c1, c2, end in segs:
            if c1 is None:
                nodes.append(GSNode(NSPoint(*end), LINE))
            else:
                nodes += [GSNode(NSPoint(*c1), OFFCURVE), GSNode(NSPoint(*c2), OFFCURVE), GSNode(NSPoint(*end), CURVE)]
            ends.append(nodes[-1])
        for j in smooth:
            ends[j].smooth = True
        gp = GSPath()
        gp.nodes = nodes
        gp.closed = True
        new_paths.append(gp)
    # only touch the layer once every contour converted, so a failure leaves the glyph as it was
    for path in replaced:
        layer.removeShape_(path)
    for gp in new_paths:
        layer.shapes.append(gp)
