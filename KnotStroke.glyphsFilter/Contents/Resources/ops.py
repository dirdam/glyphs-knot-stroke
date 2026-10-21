# encoding: utf-8
"""Glyphs <-> geometry glue: read paths, stroke them with Offset Curve, boolean with skia-pathops."""

from __future__ import division, print_function, unicode_literals
import objc
from AppKit import NSPoint, NSClassFromString
from GlyphsApp import GSLayer, GSPath, GSNode, LINE, CURVE, OFFCURVE

import math

import pathops

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
    """Stroke centerlines with the built-in Offset Curve engine; return a pathops.Path."""
    tmp = GSLayer()
    for p in path_dicts:
        if p["segments"]:
            tmp.shapes.append(build_gspath(p["segments"], p["closed"]))
    # Glyphs 3 signature (see OffsetCurve.glyphsFilter/Contents/Headers); cap is a GSLineCapStyle (0 = butt).
    OFFSET_CURVE.offsetLayer_offsetX_offsetY_makeStroke_autoStroke_position_metrics_error_shadow_capStyleStart_capStyleEnd_keepCompatibleOutlines_(
        tmp, width_x / 2.0, width_y / 2.0, True, False, 0.5, None, None, None, cap, cap, False)
    return layer_to_pathops(tmp)


def stroke_segments(segments, closed, width_x, width_y, cap=0):
    return stroke_paths([{"segments": segments, "closed": closed}], width_x, width_y, cap)


def layer_to_pathops(layer):
    result = pathops.Path()
    pen = result.getPen()
    for path in layer.paths:
        nodes = [path.nodes[i] for i in range(len(path.nodes))]
        if len(nodes) < 3 or not path.closed:
            continue
        pen.moveTo((nodes[-1].position.x, nodes[-1].position.y))
        pending = []
        for n in nodes:
            pt = (n.position.x, n.position.y)
            if n.type == OFFCURVE:
                pending.append(pt)
            elif n.type == CURVE and len(pending) == 2:
                pen.curveTo(pending[0], pending[1], pt)
                pending = []
            else:
                pen.lineTo(pt)
                pending = []
        pen.closePath()
    return result


def union(paths):
    result = pathops.Path()
    for p in paths:
        result = pathops.op(result, p, pathops.PathOp.UNION) if len(result) else p
    return result


def intersection(a, b):
    return pathops.op(a, b, pathops.PathOp.INTERSECTION)


def difference(a, b):
    return pathops.op(a, b, pathops.PathOp.DIFFERENCE)


def contour_segments(contour):
    """pathops contour -> (start point, [(c1, c2, end)]); c1 and c2 are None for a line."""
    start = prev = None
    segs = []
    for verb, pts in contour.segments:
        if verb == "moveTo":
            start = prev = pts[0]
            continue
        if verb == "lineTo":
            segs.append((None, None, pts[0]))
        elif verb == "curveTo":
            segs.append((pts[0], pts[1], pts[2]))
        elif verb == "qCurveTo":
            # quadratic -> cubic against the previous on-curve point
            c, e = pts[0], pts[-1]
            segs.append(((prev[0] + 2 * (c[0] - prev[0]) / 3, prev[1] + 2 * (c[1] - prev[1]) / 3),
                         (e[0] + 2 * (c[0] - e[0]) / 3, e[1] + 2 * (c[1] - e[1]) / 3), e))
        else:
            continue
        prev = segs[-1][2]
    if start is not None and segs and segs[-1][2] != start:
        segs.append((None, None, start))  # the implicit closing line, so the last node is the start point
    return start, segs


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


def pathops_to_layer(result, layer, replaced):
    """Replace the `replaced` paths of the layer with the contours of a pathops.Path; other paths stay."""
    new_paths = []
    for contour in result.contours:
        start, segs = contour_segments(contour)
        if start is None or len(segs) < 2:
            continue
        start, segs = drop_tiny_segments(start, segs, TINY)
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
