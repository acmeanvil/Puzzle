"""The imported-field alignment helpers (ADR 0044 decision 2.2): point pairs, matched
bounding boxes and a best fit each give a placement that `fields.place` applies back
onto the model."""

import numpy as np
import pytest

from puzzle import field_align as fa
from puzzle.fields import FieldError, place
from puzzle.model.schema import FieldPlacement


def _bar_nodes(step=0.002):
    """The nodes of a 100 x 20 x 10 mm bar on a 2 mm grid."""
    xs = np.arange(0, 0.1 + 1e-12, step)
    ys = np.arange(0, 0.02 + 1e-12, step)
    zs = np.arange(0, 0.01 + 1e-12, step)
    return np.array(np.meshgrid(xs, ys, zs, indexing="ij")).reshape(3, -1).T


@pytest.mark.parametrize(
    "degrees", [(10, -20, 35), (0, 90, 0), (0, -90, 30), (179, 5, -170), (0, 0, 0)]
)
def test_degrees_round_trip_through_the_rotation_fields_uses(degrees):
    r = fa.rotation(degrees)
    back = fa.degrees_xyz(r)
    assert np.allclose(fa.rotation(back), r, atol=1e-12)


def test_three_point_pairs_recover_a_known_placement():
    truth = FieldPlacement(rotation=(12.0, -33.0, 71.0), about=(0.01, 0.02, 0.0),
                           translation=(0.5, -0.25, 0.125))  # fmt: skip
    data = np.array([[0.0, 0.0, 0.0], [0.1, 0.0, 0.0], [0.0, 0.05, 0.02]])
    model = place(data, truth)
    fit = fa.from_point_pairs(data, model, about=truth.about)
    assert fit.rms < 1e-12 and fit.pairs == 3
    assert np.allclose(fit.placement.rotation, truth.rotation, atol=1e-9)
    assert np.allclose(fit.placement.translation, truth.translation, atol=1e-9)
    # Applied back, the placement puts every point of the bar where the truth does.
    bar = _bar_nodes()
    assert np.abs(place(bar, fit.placement) - place(bar, truth)).max() < 1e-9


def test_point_pairs_report_their_residual_and_refuse_a_line():
    data = np.array([[0.0, 0, 0], [1, 0, 0], [0, 1, 0], [0, 0, 1]])
    model = data.copy()
    model[3, 2] += 0.004  # one pick 4 mm off
    fit = fa.from_point_pairs(data, model)
    assert 0.001 < fit.rms < 0.002 and fit.largest < 0.004
    with pytest.raises(FieldError, match="one line"):
        fa.from_point_pairs([[0, 0, 0], [1, 0, 0], [2, 0, 0]], [[0, 0, 0], [1, 0, 0], [2, 0, 0]])
    with pytest.raises(FieldError, match="three point pairs"):
        fa.from_point_pairs([[0, 0, 0], [1, 0, 0]], [[0, 0, 0], [1, 0, 0]])


def test_bounding_boxes_match_exactly_on_a_box():
    box = _bar_nodes()
    moved = box + np.array([3.0, -1.0, 0.25])
    fit = fa.match_boxes(moved, box)
    assert fit.rms < 1e-15 and fit.placement.rotation == (0.0, 0.0, 0.0)
    assert np.abs(place(moved, fit.placement) - box).max() < 1e-12
    # Written in other axes (x and y swapped, z the other way round): the order option
    # turns it back so the sides line up.
    other = (box - box.mean(axis=0)) @ np.array([[0, 1, 0], [-1, 0, 0], [0, 0, 1]]).T + 7.0
    plain = fa.match_boxes(other, box)
    assert plain.rms > 0.01  # the boxes' sides don't agree
    fit = fa.match_boxes(other, box, order=True)
    assert fit.rms < 1e-12
    lo, hi = place(other, fit.placement).min(axis=0), place(other, fit.placement).max(axis=0)
    assert np.allclose(lo, box.min(axis=0), atol=1e-12)
    assert np.allclose(hi, box.max(axis=0), atol=1e-12)


def test_a_best_fit_recovers_a_small_misplacement_on_a_bars_nodes():
    bar = _bar_nodes()
    wrong = FieldPlacement(rotation=(0.4, -0.3, 0.8), about=(0.05, 0.01, 0.005),
                           translation=(0.0006, -0.0004, 0.0003))  # fmt: skip
    r, t = fa.transform_of(wrong)
    data = fa.apply(bar - t, r.T, np.zeros(3))  # the data: the bar moved by the inverse
    fit = fa.best_fit(data, bar)
    assert fit.before > 5e-4 and fit.rms < 1e-12
    assert np.abs(place(data, fit.placement) - bar).max() < 1e-9
    assert np.allclose(fit.placement.rotation, wrong.rotation, atol=1e-7)


def test_a_best_fit_onto_a_surface_starts_from_the_current_placement():
    import pyvista as pv

    surface = pv.Box(bounds=(0, 0.1, 0, 0.02, 0, 0.01), level=4).triangulate()
    rng = np.random.default_rng(1)
    points = surface.sample_points(4000) if hasattr(surface, "sample_points") else None
    # Older pyvista has no sample_points: the box's own points, refined.
    points = surface.subdivide(2).points if points is None else np.asarray(points.points)
    points = points[rng.permutation(len(points))[:1500]]
    truth = FieldPlacement(rotation=(1.0, -0.5, 2.0), translation=(0.002, 0.001, -0.001),
                           about=(0.05, 0.01, 0.005))  # fmt: skip
    r, t = fa.transform_of(truth)
    data = fa.apply(points - t, r.T, np.zeros(3))
    start = FieldPlacement(translation=(0.001, 0.0, 0.0), about=(0.05, 0.01, 0.005))
    fit = fa.best_fit(data, surface, start=start)
    assert fit.before > 1e-3 and fit.rms < 1e-6
    assert np.abs(place(data, fit.placement) - points).max() < 1e-5
