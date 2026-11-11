"""Port of RenderingTests in CoreTests.swift, plus checks for the QPainter renderer."""

import numpy as np
import pytest
from PySide6.QtGui import QColor, QImage, QPainter

from scribble import render
from scribble.core.geometry import Rect, Size
from scribble.core.models import BLUE, RED, WHITE, YELLOW, Annotation, AnnotationTool


def white(w, h):
    image = render.new_image(w, h)
    image.fill(QColor(255, 255, 255))
    return image


def px(image: QImage, x: int, y: int):
    c = image.pixelColor(x, y)
    return c.red(), c.green(), c.blue()


def ann(tool, points, color=RED, **kw):
    a = Annotation(tool=tool, points=points, color=color, start_time=0, **kw)
    a.finalize(0)
    return a


def test_render_annotated_produces_requested_size_and_draws_stroke(qapp):
    box = ann(AnnotationTool.RECTANGLE, [(50, 25), (150, 75)], id=1)
    out = render.render_annotated(white(400, 200), Size(200, 100), [box], Size(200, 100))
    assert (out.width(), out.height()) == (200, 100)
    # Pixel on the box's top edge (canvas y=25 → image row 25 from top) should be reddish.
    r, g, _ = px(out, 100, 25)
    assert r > 200 and g < 150
    # Box interior stays white.
    assert px(out, 100, 50)[1] > 240


def test_crop_rendering(qapp):
    # Left half black, right half white.
    base = render.new_image(200, 100)
    painter = QPainter(base)
    painter.fillRect(100, 0, 100, 100, QColor(255, 255, 255))
    painter.end()
    out = render.render_annotated(base, Size(200, 100), [], Size(100, 100), crop=Rect(100, 0, 100, 100))
    assert px(out, 10, 50)[0] > 240


def test_crop_keeps_annotations_in_place(qapp):
    # A box at canvas (120,20)-(180,80); cropping to x ≥ 100 at 2x puts its left edge at x=40.
    box = ann(AnnotationTool.RECTANGLE, [(120, 20), (180, 80)], id=1)
    out = render.render_annotated(
        white(200, 100), Size(200, 100), [box], Size(200, 200), crop=Rect(100, 0, 100, 100)
    )
    r, g, _ = px(out, 40, 100)
    assert r > 200 and g < 150
    assert px(out, 100, 100)[1] > 240


def test_numbered_marks_get_a_badge_and_pen_strokes_do_not(qapp):
    numbered = ann(AnnotationTool.ELLIPSE, [(100, 100), (200, 160)], color=BLUE, id=1, number=7)
    out = render.render_annotated(white(300, 300), Size(300, 300), [numbered], Size(300, 300))
    cx, cy = (int(round(v)) for v in render.badge_center(numbered))
    # Badge fill in the mark's color, with white digit pixels inside.
    region = [px(out, x, y) for x in range(cx - 8, cx + 9) for y in range(cy - 8, cy + 9)]
    assert any(b > 200 and r < 100 for r, g, b in region), "badge fill missing"
    assert any(r > 240 and g > 240 and b > 240 for r, g, b in region), "badge digit missing"

    stroke = ann(AnnotationTool.PEN, [(100, 100), (150, 120), (200, 100)], id=2)
    plain = render.render_annotated(white(300, 300), Size(300, 300), [stroke], Size(300, 300))
    bx, by = (int(round(v)) for v in render.badge_center(stroke))
    assert px(plain, bx, by) == (255, 255, 255)


def test_badge_stays_inside_canvas(qapp):
    # A box in the top-left corner would put its badge outside the image.
    box = ann(AnnotationTool.RECTANGLE, [(2, 2), (60, 40)], id=1, number=1)
    out = render.render_annotated(white(200, 100), Size(200, 100), [box], Size(200, 100))
    corner = [px(out, x, y) for x in range(2, 24) for y in range(2, 24)]
    assert any(r > 200 and g < 120 for r, g, b in corner)


def test_marks_keep_their_size_when_the_image_is_downscaled(qapp):
    # 3000 pt canvas at 0.5 px/pt: a 4 pt pen stroke must still be ~4 px wide, not 2.
    stroke = ann(AnnotationTool.ARROW, [(200, 1000), (2800, 1000)], id=1)
    out = render.render_annotated(white(3000, 2000), Size(3000, 2000), [stroke], Size(1500, 1000))
    column = [px(out, 500, y) for y in range(480, 520)]
    red_rows = [1 for r, g, b in column if r > 200 and g < 110]
    assert 3 <= len(red_rows) <= 6


def test_highlighter_is_translucent(qapp):
    mark = ann(AnnotationTool.HIGHLIGHTER, [(20, 50), (180, 50)], color=YELLOW, id=1)
    out = render.render_annotated(white(200, 100), Size(200, 100), [mark], Size(200, 100))
    r, g, b = px(out, 100, 50)
    assert r > 240 and g > 200 and 120 < b < 200  # yellow over white, text would stay visible


def test_bgra_round_trip(qapp):
    array = np.zeros((7, 5, 4), dtype=np.uint8)
    array[..., 0], array[..., 1], array[..., 2] = 10, 20, 30
    array[3, 2] = (200, 100, 50, 0)
    image = render.image_from_bgra(array)
    assert px(image, 0, 0) == (30, 20, 10)
    assert px(image, 2, 3) == (50, 100, 200)
    back = render.image_to_bgra(image)
    assert np.array_equal(back[..., :3], array[..., :3])


def test_annotation_extent_includes_badge(qapp):
    box = ann(AnnotationTool.RECTANGLE, [(100, 100), (200, 200)], id=1, number=1)
    extent = render.annotation_extent(box)
    assert extent.min_x < 100 - 10 and extent.min_y < 100 - 10
    assert extent.max_x == pytest.approx(200 + 3.5 / 2)
    pin = ann(AnnotationTool.PIN, [(50, 50)], id=2, number=2)
    assert render.annotation_extent(pin).width == pytest.approx(26)


def test_png_write_and_load(qapp, tmp_path):
    path = tmp_path / "x.png"
    render.write_png(white(20, 10), path)
    loaded = render.load_image(path)
    assert (loaded.width(), loaded.height()) == (20, 10)
    with pytest.raises(OSError):
        render.load_image(tmp_path / "missing.png")
