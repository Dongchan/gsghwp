from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import cast

import pymupdf
from reportlab.pdfgen.canvas import Canvas


SCRIPTS = (
    Path(__file__).resolve().parents[1]
    / "skills"
    / "automate-hancom-documents"
    / "scripts"
)
EXTRACTOR = SCRIPTS / "pdf_vector_extract.py"


def _write_vector_fixture(path: Path) -> None:
    canvas = Canvas(str(path), pagesize=(200, 200))
    canvas.setLineWidth(0.25)
    canvas.line(10, 180, 190, 180)
    canvas.setLineWidth(1)
    canvas.rect(10, 40, 40, 20, stroke=1, fill=0)
    canvas.rect(60, 40, 40, 20, stroke=1, fill=0)
    canvas.rect(20, 120, 80, 0.5, stroke=0, fill=1)
    canvas.bezier(20, 80, 50, 100, 80, 60, 110, 80)
    canvas.drawString(20, 160, "VECTOR")
    canvas.save()


def _write_grid_fixture(path: Path) -> None:
    canvas = Canvas(str(path), pagesize=(220, 220))
    canvas.setLineWidth(0.5)
    canvas.line(20, 40, 20, 180)
    canvas.line(140, 40, 140, 180)
    canvas.line(20, 180, 140, 180)
    canvas.line(20, 40, 140, 40)
    canvas.line(80, 110, 80, 180)

    # A filled thin rectangle connected to both vertical borders is a grid edge.
    canvas.setFillColorRGB(0, 0, 0)
    canvas.rect(20, 109.75, 120, 0.5, stroke=0, fill=1)

    # Neither a short filled marker nor an isolated elongated bar is a grid edge.
    canvas.rect(175, 35, 2, 1, stroke=0, fill=1)
    canvas.rect(170, 165, 35, 0.5, stroke=0, fill=1)

    # Only the collinear cubic is safe to approximate as a straight segment.
    canvas.setLineWidth(0.25)
    canvas.bezier(25, 40, 45, 40, 65, 40, 85, 40)
    canvas.bezier(150, 80, 165, 110, 185, 50, 205, 80)
    canvas.save()


def _write_supported_short_rule_fixture(path: Path) -> None:
    canvas = Canvas(str(path), pagesize=(100, 100))
    canvas.setLineWidth(0.5)
    canvas.line(20, 20, 20, 80)
    canvas.line(24, 20, 24, 80)
    canvas.line(20, 20, 24, 20)
    canvas.line(20, 80, 24, 80)
    canvas.rect(20, 49.75, 4, 0.5, stroke=0, fill=1)
    canvas.save()


def _write_paint_order_fixture(path: Path) -> None:
    canvas = Canvas(str(path), pagesize=(120, 120))
    canvas.setLineWidth(0.25)
    canvas.rect(20, 20, 80, 80, stroke=1, fill=0)
    canvas.setLineWidth(3)
    canvas.line(100, 20, 100, 100)
    canvas.save()


def _write_cluster_and_gap_fixture(path: Path) -> None:
    canvas = Canvas(str(path), pagesize=(120, 120))
    canvas.setLineWidth(0.5)
    canvas.line(20, 20, 20, 100)
    canvas.line(100, 20, 100, 96)
    canvas.line(20, 20, 100, 20)
    canvas.line(20, 100, 100, 100)
    canvas.line(59.8, 60, 59.8, 100)
    canvas.line(60.2, 20, 60.2, 60)
    canvas.save()


def _write_quad_fixture(path: Path) -> None:
    document = pymupdf.open()
    page = document.new_page(width=100, height=100)
    shape = page.new_shape()
    _ = shape.draw_quad(  # pyright: ignore[reportUnknownMemberType]
        pymupdf.Quad(
            pymupdf.Point(10, 20),
            pymupdf.Point(70, 20),
            pymupdf.Point(10, 80),
            pymupdf.Point(70, 80),
        )
    )
    shape.finish(  # pyright: ignore[reportUnknownMemberType]
        color=(0, 0, 0),
        width=0.5,
    )
    shape.commit()
    document.save(path)  # pyright: ignore[reportUnknownMemberType]
    document.close()


def _write_quantized_boundary_fixture(path: Path) -> None:
    canvas = Canvas(str(path), pagesize=(100, 100))
    canvas.setLineWidth(0.5)
    canvas.line(20.004, 20, 20.004, 80)
    canvas.line(20.506, 20, 20.506, 80)
    canvas.line(20.004, 20, 20.506, 20)
    canvas.line(20.004, 80, 20.506, 80)
    canvas.save()


def _extract(
    source_pdf: Path,
    output_json: Path,
) -> tuple[subprocess.CompletedProcess[str], dict[str, object]]:
    completed = subprocess.run(
        [
            sys.executable,
            str(EXTRACTOR),
            "extract",
            str(source_pdf),
            "--output",
            str(output_json),
            "--page",
            "1",
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    payload = cast(
        dict[str, object],
        (
            json.loads(output_json.read_text(encoding="utf-8"))
            if output_json.is_file()
            else {}
        ),
    )
    return completed, payload


def test_cli_extracts_vector_measurements_from_both_engines(
    tmp_path: Path,
) -> None:
    source_pdf = tmp_path / "vector-fixture.pdf"
    output_json = tmp_path / "vector-fixture.json"
    _write_vector_fixture(source_pdf)

    completed, payload = _extract(source_pdf, output_json)

    assert completed.returncode == 0, completed.stderr
    page = cast(dict[str, object], payload["page"])
    plumber = cast(dict[str, object], payload["pdfplumber"])
    summary = cast(dict[str, object], payload["summary"])
    gaps = cast(dict[str, object], summary["rect_gaps_pt"])
    mupdf = cast(dict[str, object], payload["pymupdf"])
    item_counts = cast(dict[str, object], mupdf["item_type_counts"])
    comparison = cast(dict[str, object], payload["comparison"])
    assert page["rotation"] == 0
    assert cast(int, plumber["line_count"]) >= 1
    assert plumber["thin_rect_segment_count"] == 1
    assert plumber["char_count"] == len("VECTOR")
    assert summary["chars_are_zero"] is False
    assert 10.0 in cast(list[float], gaps["horizontal"])
    assert cast(int, item_counts["c"]) >= 1
    assert cast(int, comparison["pdfplumber_line_count"]) >= 1
    assert cast(int, comparison["pymupdf_path_count"]) >= 1
    assert "pdfplumber" in completed.stdout
    assert "PyMuPDF" in completed.stdout


def test_grid_reconstruction_starts_without_edges_and_uses_segment_coverage(
    tmp_path: Path,
) -> None:
    source_pdf = tmp_path / "grid-fixture.pdf"
    output_json = tmp_path / "grid-fixture.json"
    _write_grid_fixture(source_pdf)

    completed, payload = _extract(source_pdf, output_json)

    assert completed.returncode == 0, completed.stderr
    grid = cast(dict[str, object], payload["grid_reconstruction"])
    assert grid["coordinate_tolerance_pt"] == 0.5
    assert grid["edge_coverage_threshold"] == 0.95
    assert grid["edge_initial_state"] == "none"
    assert grid["column_boundaries_pt"] == [20.0, 80.0, 140.0]
    assert grid["row_boundaries_pt"] == [40.0, 110.0, 180.0]
    assert grid["column_widths_pt"] == [60.0, 60.0]

    visible_edges = cast(list[dict[str, object]], grid["visible_edges"])
    vertical = {
        (
            cast(int, edge["line"]),
            cast(int, edge["start"]),
            cast(int, edge["end"]),
        )
        for edge in visible_edges
        if edge["orientation"] == "vertical"
    }
    assert (1, 0, 1) in vertical
    assert (1, 1, 2) not in vertical
    assert cast(int, grid["omitted_edge_count"]) > 0

    thin_rects = cast(dict[str, object], grid["thin_rect_classification"])
    assert thin_rects["geometry_candidate_count"] == 2
    assert thin_rects["grid_segment_count"] == 1
    assert thin_rects["rejected_short_or_low_aspect_count"] == 1
    assert thin_rects["rejected_without_grid_support_count"] == 1

    beziers = cast(dict[str, object], grid["bezier_policy"])
    assert beziers["stroke_bezier_count"] == 2
    assert beziers["straight_approximation_count"] == 1
    assert beziers["curved_excluded_count"] == 1


def test_grid_reconstruction_uses_topology_for_short_rules(
    tmp_path: Path,
) -> None:
    source_pdf = tmp_path / "supported-short-rule.pdf"
    output_json = tmp_path / "supported-short-rule.json"
    _write_supported_short_rule_fixture(source_pdf)

    completed, payload = _extract(source_pdf, output_json)

    assert completed.returncode == 0, completed.stderr
    grid = cast(dict[str, object], payload["grid_reconstruction"])
    thin_rects = cast(dict[str, object], grid["thin_rect_classification"])
    assert thin_rects["grid_segment_count"] == 1
    assert thin_rects["supported_short_rule_count"] == 1


def test_grid_reconstruction_uses_final_paint_width_and_quad_perimeter(
    tmp_path: Path,
) -> None:
    paint_pdf = tmp_path / "paint-order.pdf"
    paint_json = tmp_path / "paint-order.json"
    _write_paint_order_fixture(paint_pdf)
    completed, payload = _extract(paint_pdf, paint_json)

    assert completed.returncode == 0, completed.stderr
    grid = cast(dict[str, object], payload["grid_reconstruction"])
    visible_edges = cast(list[dict[str, object]], grid["visible_edges"])
    overpainted = next(
        edge
        for edge in visible_edges
        if edge["orientation"] == "vertical" and edge["coordinate_pt"] == 100.0
    )
    assert overpainted["linewidths_pt"] == [0.25, 3.0]
    assert overpainted["dominant_linewidth_pt"] == 3.0

    quad_pdf = tmp_path / "quad.pdf"
    quad_json = tmp_path / "quad.json"
    _write_quad_fixture(quad_pdf)
    completed, payload = _extract(quad_pdf, quad_json)

    assert completed.returncode == 0, completed.stderr
    grid = cast(dict[str, object], payload["grid_reconstruction"])
    assert grid["column_boundaries_pt"] == [10.0, 70.0]
    assert grid["row_boundaries_pt"] == [20.0, 80.0]
    assert grid["visible_edge_count"] == 4


def test_grid_reconstruction_uses_unbiased_clusters_and_rejects_real_gaps(
    tmp_path: Path,
) -> None:
    source_pdf = tmp_path / "cluster-gap.pdf"
    output_json = tmp_path / "cluster-gap.json"
    _write_cluster_and_gap_fixture(source_pdf)

    completed, payload = _extract(source_pdf, output_json)

    assert completed.returncode == 0, completed.stderr
    grid = cast(dict[str, object], payload["grid_reconstruction"])
    assert grid["column_boundaries_pt"] == [20.0, 60.0, 100.0]
    visible_edges = cast(list[dict[str, object]], grid["visible_edges"])
    assert not any(
        edge["orientation"] == "vertical" and edge["coordinate_pt"] == 100.0
        for edge in visible_edges
    )
    assert grid["edge_max_uncovered_gap_pt"] == 0.5


def test_cli_records_direct_hwpunit_geometry_and_border_snap_error(
    tmp_path: Path,
) -> None:
    source_pdf = tmp_path / "grid-units.pdf"
    output_json = tmp_path / "grid-units.json"
    _write_grid_fixture(source_pdf)

    completed, payload = _extract(source_pdf, output_json)

    assert completed.returncode == 0, completed.stderr
    grid = cast(dict[str, object], payload["grid_reconstruction"])
    assert grid["column_boundaries_hwpunit"] == [2000, 8000, 14000]
    assert grid["column_widths_hwpunit"] == [6000, 6000]

    alignment = cast(dict[str, object], payload["unit_alignment"])
    assert alignment["pt_to_hwpunit"] == "round_half_up(Decimal(str(pt)) * 100)"
    snap_table = cast(list[dict[str, object]], alignment["linewidth_snap_table"])
    half_point = next(item for item in snap_table if item["source_linewidth_pt"] == 0.5)
    assert half_point["source_hwpunit"] == 50
    assert half_point["snapped_border_width"] == "0.2mm"
    assert "residual_mm" in half_point

    visible_edges = cast(list[dict[str, object]], grid["visible_edges"])
    assert visible_edges
    assert all("border_width_snap" in edge for edge in visible_edges)
    distribution = cast(dict[str, object], alignment["snap_error_distribution"])
    assert "median_absolute_error_mm" in distribution
    assert "p95_absolute_error_mm" in distribution


def test_grid_hwpunit_widths_are_differences_of_quantized_boundaries(
    tmp_path: Path,
) -> None:
    source_pdf = tmp_path / "quantized-boundaries.pdf"
    output_json = tmp_path / "quantized-boundaries.json"
    _write_quantized_boundary_fixture(source_pdf)

    completed, payload = _extract(source_pdf, output_json)

    assert completed.returncode == 0, completed.stderr
    grid = cast(dict[str, object], payload["grid_reconstruction"])
    boundaries = cast(list[int], grid["column_boundaries_hwpunit"])
    widths = cast(list[int], grid["column_widths_hwpunit"])
    assert widths == [right - left for left, right in zip(boundaries, boundaries[1:])]
