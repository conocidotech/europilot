"""Uniform-grid spatial index for the tile derivation's spatial joins.

build_tiles_full stamps every road with residential membership, calming/crossing
density and parallel-cycleway sidedness -- each a join against every polygon,
node or cycleway in the input. On a dense national extract that is O(roads x
features) and does not finish. This bins the features into a lon/lat grid so each
road only tests the features in the cells its bounding box touches; the join
functions then run unchanged on that candidate subset.

Correctness by construction: a feature is registered in every grid cell its
geometry occupies, and a query returns every feature in the cells overlapping the
road's bbox padded by one cell (~1.1 km, dwarfing any join's 15-20 m search
radius). So the candidate set is always a superset of what a full scan would
consider, and the join functions do the real distance test -- results, and
therefore tile hashes, are byte-identical to the full scan.
"""

CELL_DEG = 0.01   # ~1.1 km per cell; far larger than the joins' 15-20 m radii


def _cell(x: float, y: float) -> tuple[int, int]:
    return (int(x / CELL_DEG), int(y / CELL_DEG))


def _bbox(coords: list) -> tuple[float, float, float, float]:
    xs = [p[0] for p in coords]
    ys = [p[1] for p in coords]
    return min(xs), min(ys), max(xs), max(ys)


def _cells_over(bbox: tuple[float, float, float, float], pad_cells: int):
    x0, y0, x1, y1 = bbox
    for cx in range(int(x0 / CELL_DEG) - pad_cells, int(x1 / CELL_DEG) + pad_cells + 1):
        for cy in range(int(y0 / CELL_DEG) - pad_cells, int(y1 / CELL_DEG) + pad_cells + 1):
            yield (cx, cy)


class PointIndex:
    """Bins (x, y) points; near() returns those in cells around a road's bbox."""

    def __init__(self, points: list):
        self._grid: dict[tuple[int, int], list] = {}
        for p in points:
            self._grid.setdefault(_cell(p[0], p[1]), []).append(p)

    def near(self, coords: list) -> list:
        if not coords:
            return []
        out: list = []
        for c in _cells_over(_bbox(coords), 1):
            out.extend(self._grid.get(c, ()))
        return out


class PolylineIndex:
    """Bins polylines (e.g. cycleways) by every cell their points occupy."""

    def __init__(self, polylines: list[list]):
        self._items = list(polylines)
        self._grid: dict[tuple[int, int], set[int]] = {}
        for i, pl in enumerate(self._items):
            for p in pl:
                self._grid.setdefault(_cell(p[0], p[1]), set()).add(i)

    def near(self, coords: list) -> list[list]:
        if not coords:
            return []
        ids: set[int] = set()
        for c in _cells_over(_bbox(coords), 1):
            ids |= self._grid.get(c, frozenset())
        return [self._items[i] for i in ids]


class PolygonIndex:
    """Bins polygons by every cell their outer-ring bbox covers."""

    def __init__(self, polygons: list):
        self._items = list(polygons)
        self._grid: dict[tuple[int, int], set[int]] = {}
        for i, (outer, _holes) in enumerate(self._items):
            if not outer:
                continue
            for c in _cells_over(_bbox(outer), 0):
                self._grid.setdefault(c, set()).add(i)

    def near(self, coords: list) -> list:
        if not coords:
            return []
        ids: set[int] = set()
        for c in _cells_over(_bbox(coords), 1):
            ids |= self._grid.get(c, frozenset())
        return [self._items[i] for i in ids]
