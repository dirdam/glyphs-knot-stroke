# encoding: utf-8
"""Glyphs <-> geometry glue: read paths, stroke them with Offset Curve, boolean with skia-pathops."""

from __future__ import division, print_function, unicode_literals
import objc
from AppKit import NSPoint, NSClassFromString
from GlyphsApp import GSLayer, GSPath, GSNode, LINE, CURVE, OFFCURVE

import pathops

OFFSET_CURVE = NSClassFromString("GlyphsFilterOffsetCurve")


def read_paths(layer):
    """Layer paths -> knot.py path dicts. Raises ValueError on quadratic curves."""
    out = []
    for pi in range(len(layer.paths)):
        path = layer.paths[pi]
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


def pathops_to_layer(result, layer):
    """Replace layer's paths with the contours of a pathops.Path."""
    for path in [layer.paths[i] for i in range(len(layer.paths))]:
        layer.removeShape_(path)
    for contour in result.contours:
        nodes = []
        for verb, pts in contour.segments:
            if verb == "moveTo":
                continue
            if verb == "lineTo":
                nodes.append(GSNode(NSPoint(*pts[0]), LINE))
            elif verb == "curveTo":
                nodes += [GSNode(NSPoint(*pts[0]), OFFCURVE), GSNode(NSPoint(*pts[1]), OFFCURVE),
                          GSNode(NSPoint(*pts[2]), CURVE)]
            elif verb == "qCurveTo":
                # quadratic -> cubic against the previous on-curve point
                prev = (nodes[-1].position.x, nodes[-1].position.y) if nodes else contour.start_point
                c, e = pts[0], pts[-1]
                nodes += [GSNode(NSPoint(prev[0] + 2 * (c[0] - prev[0]) / 3, prev[1] + 2 * (c[1] - prev[1]) / 3), OFFCURVE),
                          GSNode(NSPoint(e[0] + 2 * (c[0] - e[0]) / 3, e[1] + 2 * (c[1] - e[1]) / 3), OFFCURVE),
                          GSNode(NSPoint(*e), CURVE)]
        if len(nodes) < 2:
            continue
        gp = GSPath()
        gp.nodes = nodes
        gp.closed = True
        layer.shapes.append(gp)
