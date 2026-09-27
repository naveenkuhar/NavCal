"""Toolkit-independent layout helpers for the calendar views."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from .models import Occurrence

MIN_BLOCK = timedelta(minutes=25)  # smallest visual height of a timed event
DRAG_THRESHOLD = 6  # px the pointer must travel before a press becomes a drag


def layout_columns(segs):
    """Side-by-side layout for overlapping timed segments.

    segs: [(occ, start, end)] within one day.
    Returns [(occ, start, end, column, column_count)].
    """
    segs = sorted(segs, key=lambda t: (t[1], -(t[2] - t[1])))
    out, cluster, col_ends, cluster_end = [], [], [], None

    def flush():
        out.extend((*item, len(col_ends)) for item in cluster)

    for occ, s, e in segs:
        visual_end = max(e, s + MIN_BLOCK)
        if cluster and s >= cluster_end:
            flush()
            cluster, col_ends = [], []
        for col, end in enumerate(col_ends):
            if end <= s:
                col_ends[col] = visual_end
                break
        else:
            col = len(col_ends)
            col_ends.append(visual_end)
        cluster_end = visual_end if not cluster else max(cluster_end, visual_end)
        cluster.append((occ, s, e, col))
    if cluster:
        flush()
    return out


def assign_lanes(spans):
    """Stack multi-day bars into lanes without overlap.

    spans: [(occ, first_col, last_col)]. Returns ([(occ, first, last, lane)], lane_count).
    """
    spans = sorted(spans, key=lambda t: (t[1], t[1] - t[2]))
    lane_ends: list[int] = []
    items = []
    for occ, c0, c1 in spans:
        lane = next((i for i, end in enumerate(lane_ends) if end < c0), None)
        if lane is None:
            lane = len(lane_ends)
            lane_ends.append(c1)
        else:
            lane_ends[lane] = c1
        items.append((occ, c0, c1, lane))
    return items, len(lane_ends)


@dataclass
class DragState:
    """An in-progress drag of an event (move or resize)."""

    occ: Occurrence
    anchor: object  # where the drag began: a slot datetime, column index or date
    mode: str = "move"  # or "resize"
    active: bool = False  # becomes True once the pointer has moved far enough
    start: datetime | None = None
    end: datetime | None = None
    crossed: bool = False  # dragged between the all-day row and the time grid
    grab: tuple | None = None  # (x, y, w, h) of the pointer within the grabbed event, and its size

    def update_active(self, dx: float, dy: float) -> bool:
        if not self.active:
            self.active = abs(dx) + abs(dy) >= DRAG_THRESHOLD
        return self.active

    @property
    def has_target(self) -> bool:
        """The pointer has been over somewhere the event can go."""
        return self.start is not None and self.end is not None

    @property
    def changed(self) -> bool:
        return self.active and self.has_target and (
            self.crossed or (self.start, self.end) != (self.occ.start, self.occ.end))

    def preview(self) -> Occurrence:
        return Occurrence(self.occ.event, self.start, self.end, color=self.occ.color)
