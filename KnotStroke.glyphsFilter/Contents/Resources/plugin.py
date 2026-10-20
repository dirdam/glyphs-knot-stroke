# encoding: utf-8

###########################################################################################################
#
# Knot Stroke
#
# Strokes the paths of a layer with the Offset Curve engine and cuts the under-strand at every crossing,
# so the result reads as a knot with alternating over/under crossings.
#
###########################################################################################################

from __future__ import division, print_function, unicode_literals
import os
import sys
import traceback

import objc
from GlyphsApp import Glyphs
from GlyphsApp.plugins import FilterWithDialog
from AppKit import NSImage, NSBezierPath, NSColor, NSMakeSize, NSStepper, NSObject, NSTextField, NSTextFieldCell
from vanilla import Group, TextBox, EditText, CheckBox, ImageButton, ImageView, SegmentedButton
from vanilla.vanillaEditText import VanillaEditTextDelegate

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import knot

try:
	import ops
	IMPORT_ERROR = None
except Exception:  # most likely: skia-pathops is not installed in Glyphs' Python
	ops = None
	IMPORT_ERROR = traceback.format_exc()

PREFIX = "com.dirdam.KnotStroke."
LOG_PATH = os.path.expanduser("~/Library/Logs/KnotStroke.log")


def log(message):
	"""Append to ~/Library/Logs/KnotStroke.log; the Macro window is easy to miss."""
	try:
		with open(LOG_PATH, "a") as f:
			f.write("%s\n" % message)
	except Exception:
		pass

DEFAULTS = {"horizontal": 40.0, "vertical": 40.0, "gap": 14.0, "flip": False, "linked": True, "cap": 0}


# Segment order matches Offset Curve's end buttons; values are Glyphs' GSLineCapStyle.
CAP_STYLES = [0, 3, 1, 2, 4]  # butt, square, round, round inset, align to axis


def cap_icon(index):
	"""Icon of a stroke end like Offset Curve's buttons: the stroke runs off to the right, its end node is on the left."""
	x0, y0, half, right = 11, 11, 8, 23  # end node, half the stroke width, right edge of the stroke

	def draw(rect):
		body = NSBezierPath.bezierPath()
		line_from = (right + 1, y0)
		node = (x0, y0)
		if index == 0:  # butt: the stroke starts at the node
			points = [(x0, y0 - half), (right, y0 - half), (right, y0 + half), (x0, y0 + half)]
		elif index == 1:  # square: extended by half the width
			points = [(x0 - half, y0 - half), (right, y0 - half), (right, y0 + half), (x0 - half, y0 + half)]
		elif index == 4:  # align to axis: diagonal path, end cut parallel to the x axis
			# horizontal base through the node, the sides run parallel to the path, the far end is cut square to it
			node = (14, 4)
			line_from = (8.7, 12.8)
			points = [(4.6, 4), (2.4, 9.3), (16.2, 16.8), (22.8, 4)]
		else:
			points = None
		if points:
			body.moveToPoint_(points[0])
			for p in points[1:]:
				body.lineToPoint_(p)
		elif index == 2:  # round: half circle around the node
			body.moveToPoint_((right, y0 - half))
			body.lineToPoint_((x0, y0 - half))
			body.appendBezierPathWithArcWithCenter_radius_startAngle_endAngle_clockwise_((x0, y0), half, -90, 90, True)
			body.lineToPoint_((right, y0 + half))
		else:  # round inset: a shallow arc, the node sits on its tip
			radius = 8.9
			body.moveToPoint_((right, y0 - half))
			body.appendBezierPathWithArcWithCenter_radius_startAngle_endAngle_clockwise_((x0 - 1 + radius, y0), radius, 244, 116, True)
			body.lineToPoint_((right, y0 + half))
		body.closePath()
		NSColor.colorWithCalibratedWhite_alpha_(0.5, 0.8).set()
		body.fill()
		NSColor.labelColor().set()
		line = NSBezierPath.bezierPath()
		line.moveToPoint_(line_from)
		line.lineToPoint_(node)
		line.setLineWidth_(1.2)
		line.stroke()
		dot = NSBezierPath.bezierPathWithOvalInRect_(((node[0] - 2.5, node[1] - 2.5), (5, 5)))
		NSColor.textBackgroundColor().set()
		dot.fill()
		NSColor.labelColor().set()
		dot.setLineWidth_(1.2)
		dot.stroke()
		return True
	image = NSImage.imageWithSize_flipped_drawingHandler_(NSMakeSize(26, 22), False, draw)
	image.setSize_(NSMakeSize(21, 18))  # the drawing scales down to fit a compact bar
	return image


def gap_icon():
	"""Two short vertical bars with a clear space between them, matching the weight of the axis arrows."""
	def draw(rect):
		NSColor.labelColor().set()
		for x in (3.5, 12.5):
			bar = NSBezierPath.bezierPath()
			bar.moveToPoint_((x, 3))
			bar.lineToPoint_((x, 14))
			bar.setLineWidth_(1.5)
			bar.setLineCapStyle_(1)
			bar.stroke()
		return True
	return NSImage.imageWithSize_flipped_drawingHandler_(NSMakeSize(16, 17), False, draw)


def fmt(value):
	return "%g" % value


class StepDelegate(VanillaEditTextDelegate):
	"""Up/down arrow in a number field adds/subtracts 1, with Shift 10 (as in Glyphs)."""

	def control_textView_doCommandBySelector_(self, control, textView, selector):
		step = {"moveUp:": 1, "moveDown:": -1, "moveUpAndModifySelection:": 10, "moveDownAndModifySelection:": -10}.get(selector)
		if step is None:
			return False
		try:
			value = float(control.stringValue().replace(",", "."))
		except ValueError:
			value = 0.0
		control.setStringValue_(fmt(max(0.0, value + step)))
		self.action_(control)
		return True


class InsetCell(NSTextFieldCell):
	"""Leaves room inside the field for the symbol on the left and the stepper on the right."""
	left = 24
	right = 20
	editing = False  # the frame handed to the editor is already inset: don't inset it a second time

	@objc.python_method
	def inset(self, rect):
		(x, y), (w, h) = rect
		return ((x + self.left, y), (w - self.left - self.right, h))

	def drawingRectForBounds_(self, rect):
		rect = objc.super(InsetCell, self).drawingRectForBounds_(rect)
		return rect if self.editing else self.inset(rect)

	def editWithFrame_inView_editor_delegate_event_(self, rect, view, editor, delegate, event):
		self.editing = True
		try:
			objc.super(InsetCell, self).editWithFrame_inView_editor_delegate_event_(self.inset(rect), view, editor, delegate, event)
		finally:
			self.editing = False

	def selectWithFrame_inView_editor_delegate_start_length_(self, rect, view, editor, delegate, start, length):
		self.editing = True
		try:
			objc.super(InsetCell, self).selectWithFrame_inView_editor_delegate_start_length_(self.inset(rect), view, editor, delegate, start, length)
		finally:
			self.editing = False

	def resetCursorRect_inView_(self, rect, view):
		# the text cursor only over the number, not over the symbol and the stepper
		self.editing = True
		try:
			objc.super(InsetCell, self).resetCursorRect_inView_(self.inset(rect), view)
		finally:
			self.editing = False


class InsetTextField(NSTextField):
	@classmethod
	def cellClass(cls):
		return InsetCell


class StepEditText(EditText):
	nsTextFieldClass = InsetTextField
	nsTextFieldDelegateClass = StepDelegate


class StepperTarget(NSObject):
	"""Receives the clicks of the up/down steppers next to the number fields."""

	def initWithPlugin_(self, plugin):
		self = objc.super(StepperTarget, self).init()
		self.plugin = plugin
		return self

	@objc.IBAction
	def stepped_(self, sender):
		self.plugin.stepperClicked(sender)


class KnotStroke(FilterWithDialog):

	# No class-level `dialog` IBOutlet: with a Vanilla dialog Glyphs expects it to be set in settings().
	@objc.python_method
	def settings(self):
		self.menuName = Glyphs.localize({
			'en': 'Knot Stroke',
			'es': 'Trazo de nudo',
			'de': 'Knotenkontur',
			'fr': 'Contour noué',
			'jp': '結び目ストローク',
		})
		self.actionButtonLabel = Glyphs.localize({'en': 'Apply', 'es': 'Aplicar', 'de': 'Anwenden', 'fr': 'Appliquer', 'jp': '適用'})

		for key, value in DEFAULTS.items():
			Glyphs.registerDefault(PREFIX + key, value)

		try:
			self.group = Group((0, 0, 250, 176))
			# columns: label (right-aligned), then the number field holding its symbol on the left and its stepper on the right
			self.group.hLabel = TextBox((6, 12, 100, 17), "Horizontal", alignment="right")
			self.group.hField = StepEditText((108, 10, 72, 21), "", callback=self.fieldChanged)
			self.group.vLabel = TextBox((6, 40, 100, 17), "Vertical", alignment="right")
			self.group.vField = StepEditText((108, 38, 72, 21), "", callback=self.fieldChanged)
			self.group.lock = ImageButton((182, 24, 20, 22), bordered=False, callback=self.lockClicked)
			self.group.gLabel = TextBox((6, 76, 100, 17), "Gap", alignment="right")
			self.group.gField = StepEditText((108, 74, 72, 21), "", callback=self.fieldChanged)
			self.group.hSymbol = ImageView((113, 12, 16, 17))
			self.group.vSymbol = ImageView((113, 40, 16, 17))
			self.group.gSymbol = ImageView((113, 76, 16, 17))
			self.group.flipLabel = TextBox((6, 104, 100, 17), "Invert", alignment="right")
			self.group.flip = CheckBox((108, 104, 24, 20), "", callback=self.fieldChanged)
			self.group.cap = SegmentedButton((30, 134, 190, 30), [{"imageObject": cap_icon(i), "imageTemplate": False, "width": 36} for i in range(len(CAP_STYLES))],
				callback=self.fieldChanged, sizeStyle="regular")
			self.group.cap.getNSSegmentedButton().setSegmentStyle_(0)  # automatic: the rounded gray track with a white pill
			self.dialog = self.group.getNSView()
			self.dialog.setFrame_(((0, 0), (250, 176)))
			for key, name in (("hSymbol", "arrow.left.and.right"), ("vSymbol", "arrow.up.and.down")):
				view = getattr(self.group, key).getNSImageView()
				view.setImage_(NSImage.imageWithSystemSymbolName_accessibilityDescription_(name, None))
				view.setContentTintColor_(NSColor.labelColor())
			self.group.gSymbol.getNSImageView().setImage_(gap_icon())
			self.stepperTarget = StepperTarget.alloc().initWithPlugin_(self)
			self.steppers = {}
			for key, y in (("hField", 10), ("vField", 38), ("gField", 74)):
				stepper = NSStepper.alloc().initWithFrame_(((0, 0), (19, 27)))
				stepper.setControlSize_(2)  # mini: small enough to sit inside the 21 px field
				stepper.sizeToFit()
				size = stepper.frame().size
				margin = (21 - size.height) / 2.0  # same gap above, below and to the right of the stepper
				x = 108 + 72 - size.width - margin
				InsetCell.right = size.width + margin + 2
				stepper.setMinValue_(0)
				stepper.setMaxValue_(100000)
				stepper.setIncrement_(1)
				stepper.setValueWraps_(False)
				stepper.setTarget_(self.stepperTarget)
				stepper.setAction_("stepped:")
				stepper.setFrameOrigin_((x, y + margin))
				if not self.dialog.isFlipped():
					stepper.setFrameOrigin_((x, 176 - y - 21 + margin))
				self.dialog.addSubview_(stepper)
				self.steppers[key] = stepper
			log("dialog built: %s frame=%s subviews=%d" % (self.dialog, self.dialog.frame(), len(self.dialog.subviews())))
		except Exception:
			log("dialog failed:\n" + traceback.format_exc())

	@objc.python_method
	def start(self):
		d = getattr(self, "dialog", None)
		log("start: dialog=%s frame=%s window=%s superview=%s" % (d, d.frame() if d else None, d.window() if d else None, d.superview() if d else None))
		self.group.hField.set(fmt(Glyphs.defaults[PREFIX + "horizontal"]))
		self.group.vField.set(fmt(Glyphs.defaults[PREFIX + "vertical"]))
		self.group.gField.set(fmt(Glyphs.defaults[PREFIX + "gap"]))
		self.group.flip.set(bool(Glyphs.defaults[PREFIX + "flip"]))
		self.group.cap.set(int(Glyphs.defaults[PREFIX + "cap"]))
		# Glyphs pins the dialog view to the left of a wider window: center the end buttons in the window itself
		try:
			container = self.dialog.superview()
			control = self.group.cap.getNSSegmentedButton()
			frame = control.frame()
			if container is not None:
				frame.origin.x = (container.frame().size.width - frame.size.width) / 2.0 - self.dialog.frame().origin.x
				control.setFrame_(frame)
				log("centered buttons: container width %s" % container.frame().size.width)
		except Exception:
			log("centering failed:\n" + traceback.format_exc())
		self.showLock(bool(Glyphs.defaults[PREFIX + "linked"]))

	@objc.python_method
	def syncSteppers(self):
		for key, stepper in self.steppers.items():
			try:
				stepper.setDoubleValue_(float(getattr(self.group, key).get().replace(",", ".")))
			except ValueError:
				pass
		self.steppers["vField"].setEnabled_(not self.linked)

	@objc.python_method
	def stepperClicked(self, sender):
		for key, stepper in self.steppers.items():
			if stepper is sender:
				getattr(self.group, key).set(fmt(stepper.doubleValue()))
		self.fieldChanged(sender)

	@objc.python_method
	def showLock(self, linked):
		"""Lock icon state; while locked the vertical field is grayed out and mirrors horizontal."""
		self.linked = linked
		name = "lock.fill" if linked else "lock.open"
		image = NSImage.imageWithSystemSymbolName_accessibilityDescription_(name, "Link horizontal and vertical")
		self.group.lock.getNSButton().setImage_(image)
		self.group.vField.enable(not linked)
		if linked:
			self.group.vField.set(self.group.hField.get())
		self.syncSteppers()

	@objc.python_method
	def lockClicked(self, sender):
		self.showLock(not self.linked)
		self.fieldChanged(sender)

	@objc.python_method
	def fieldChanged(self, sender):
		g = self.group
		linked = self.linked
		if linked:
			g.vField.set(g.hField.get())
		for field, key in ((g.hField, "horizontal"), (g.vField, "vertical"), (g.gField, "gap")):
			try:
				value = float(field.get().replace(",", "."))
			except ValueError:
				continue
			if value >= 0:
				Glyphs.defaults[PREFIX + key] = value
		Glyphs.defaults[PREFIX + "flip"] = bool(g.flip.get())
		Glyphs.defaults[PREFIX + "cap"] = int(g.cap.get())
		Glyphs.defaults[PREFIX + "linked"] = linked
		self.syncSteppers()
		self.update()

	@objc.python_method
	def readSettings(self, customParameters):
		"""Custom parameter values (export) win over the stored dialog values."""
		def number(key):
			if key in customParameters:
				return float(customParameters[key])
			return float(Glyphs.defaults[PREFIX + key])
		flip = bool(int(float(customParameters["flip"]))) if "flip" in customParameters else bool(Glyphs.defaults[PREFIX + "flip"])
		if "cap" in customParameters:
			cap = CAP_STYLES[int(float(customParameters["cap"])) % len(CAP_STYLES)]
		else:
			cap = CAP_STYLES[int(Glyphs.defaults[PREFIX + "cap"]) % len(CAP_STYLES)]
		return number("horizontal"), number("vertical"), number("gap"), flip, cap

	@objc.python_method
	def filter(self, layer, inEditView, customParameters):
		log("filter called; ops=%s inEditView=%s params=%s" % (ops is not None, inEditView, dict(customParameters)))
		if ops is None:
			log(IMPORT_ERROR)
			Glyphs.showNotification("Knot Stroke", "Install skia-pathops in Glyphs > Settings > Addons > Modules.")
			print(IMPORT_ERROR)
			return
		width_x, width_y, gap, flip, cap = self.readSettings(customParameters)
		log("widths %s %s gap %s flip %s cap %s; layer paths=%d" % (width_x, width_y, gap, flip, cap, len(layer.paths)))
		if width_x <= 0 or width_y <= 0:
			return
		try:
			paths = ops.read_paths(layer)
			log("read_paths -> %d paths, segments %s" % (len(paths), [len(p["segments"]) for p in paths]))
			if not paths:
				return
			full = ops.stroke_paths(paths, width_x, width_y, cap)
			log("stroke done: %d contours" % len(list(full.contours)))
			crossings = knot.assign_over_under(paths, knot.find_crossings(paths), flip=flip)
			log("crossings: %d" % len(crossings))

			# Per crossing, cut away only the under strand's material inside a band around the over strand
			# (over strand's width plus the gap on each side), and never touch the over strand itself.
			# Nothing is re-added, so no butt-ended pieces end up glued onto the outline.
			width = max(width_x, width_y)
			cuts = []
			for c in crossings:
				pa, sa = c["a"]
				pb, sb = c["b"]
				sine = max(knot.crossing_sine(paths[pa], sa, paths[pb], sb), 0.25)
				extent = (width + gap) / sine + width
				band = ops.stroke_segments(knot.over_piece(paths, c, extent), False, width_x + 2 * gap, width_y + 2 * gap, cap)
				under = ops.stroke_segments(knot.under_piece(paths, c, extent), False, width_x, width_y, cap)
				# 1 unit wider than the real stroke so the cut leaves no slivers along the over strand's edges
				over = ops.stroke_segments(knot.over_piece(paths, c, extent), False, width_x + 2, width_y + 2, cap)
				cuts.append(ops.difference(ops.intersection(band, under), over))

			result = ops.difference(full, ops.union(cuts)) if cuts else full
			ops.pathops_to_layer(result, layer)
			log("done: %d contours written" % len(list(result.contours)))
		except Exception:
			log(traceback.format_exc())
			print(traceback.format_exc())
			Glyphs.showNotification("Knot Stroke", "Failed; see the Macro window for details.")

	@objc.python_method
	def generateCustomParameter(self):
		return "%s; horizontal:%s; vertical:%s; gap:%s; flip:%d; cap:%d;" % (
			self.__class__.__name__,
			Glyphs.defaults[PREFIX + "horizontal"], Glyphs.defaults[PREFIX + "vertical"],
			Glyphs.defaults[PREFIX + "gap"], int(bool(Glyphs.defaults[PREFIX + "flip"])),
			int(Glyphs.defaults[PREFIX + "cap"]))

	@objc.python_method
	def __file__(self):
		"""Please leave this method unchanged"""
		return __file__
