"""Alternate builds from one parts inventory ("the box").

Two jobs:

* ``parts_match``: compare a build with a box (another build, a saved box or a
  parts list) and report missing parts, leftovers and hidden filler.
* ``pair_file``: write a Better Destruction ``.bdpair.json`` that says which
  physical brick of set 1 becomes which brick of set 2.

Bricks placed inside a section whose FILE name contains ``hidden`` (for
example ``stage-hidden-filler.ldr``) are hidden filler: real parts that are
tucked out of sight so both builds can use the whole box.
"""
from __future__ import annotations

import csv
import json
from collections import Counter
from pathlib import Path

import numpy as np

from .common import atomic_write, dumps, jsonable

HIDDEN_MARK = "hidden"
DEFAULT_HIDDEN_PERCENT = 10.0


# ---------------------------------------------------------------------------
#  Loading builds and boxes
# ---------------------------------------------------------------------------

def _part_key(code):
    code = str(code).strip().replace("\\", "/").casefold()
    return code[:-4] if code.endswith(".dat") else code


def _colour_code(colour):
    code = getattr(colour, "code", colour)
    if code is None:
        rgb = getattr(colour, "rgb", None)
        return str(rgb) if rgb else "unknown"
    return int(code) if str(code).lstrip("-").isdigit() else str(code)


def load_model(path, parts, instance_limit=100000):
    """A checked model from an .mpd/.ldr or a JSON plan, as a physical view."""
    from .document import physical_context
    path = Path(path)
    suffix = path.suffix.casefold()
    if suffix == ".json":
        from .builder import build_plan, load_plan
        _text, model, diagnostics = build_plan(load_plan(path), parts, instance_limit=instance_limit)
    elif suffix in {".mpd", ".ldr"}:
        from .validation import validate_file
        model, diagnostics = validate_file(path, parts, assembly=True, instance_limit=instance_limit)
    else:
        raise ValueError(f"{path}: expected an .mpd/.ldr model or a .json plan")
    errors = [d for d in diagnostics if d.get("severity") == "error"]
    if model is None or errors:
        detail = "; ".join(d.get("message", d.get("code", "")) for d in errors[:3])
        raise ValueError(f"{path}: model has errors, fix it first ({detail})")
    return physical_context(model, parts)


def _hidden(occurrence):
    for item in occurrence.path:
        name = getattr(item.model, "name", "") or ""
        if HIDDEN_MARK in name.casefold():
            return True
    return False


def bricks(model, parts):
    """Every physical part placement with world transform and body centre."""
    out, cache = [], {}
    for occ in model.iter_occurrences(include_steps=True):
        part = _part_key(occ.part_code)
        pos = np.array(jsonable(occ.position), dtype=float)
        rot = np.array(jsonable(occ.matrix), dtype=float)
        if part not in cache:
            try:
                bounds = parts.geometry(part).bounds
            except Exception:  # noqa: BLE001 - a missing geometry only loses the centre
                bounds = None
            cache[part] = None if bounds is None else (
                (np.array(jsonable(bounds.min)) + np.array(jsonable(bounds.max))) / 2,
                np.array(jsonable(bounds.min)), np.array(jsonable(bounds.max)))
        local = cache[part]
        if local is None:
            centre, bottom = pos, pos[1]
        else:
            centre = pos + rot @ local[0]
            corners = np.array([[x, y, z] for x in (local[1][0], local[2][0])
                                for y in (local[1][1], local[2][1])
                                for z in (local[1][2], local[2][2])])
            bottom = float((corners @ rot.T + pos)[:, 1].max())   # -Y is up
        out.append(dict(part=part, colour=_colour_code(occ.colour), pos=pos, rot=rot,
                        center=centre, bottom=bottom, step=occ.step or 0,
                        hidden=_hidden(occ), source_line=occ.source_line))
    return out


def _counter(rows):
    return Counter((r["part"], r["colour"]) for r in rows)


def _rows_counter(rows, where):
    counts = Counter()
    for n, row in enumerate(rows, 1):
        part = row.get("part") or row.get("Part") or row.get("ref")
        colour = row.get("colour", row.get("color", row.get("colour_code", row.get("Color", row.get("Colour")))))
        quantity = row.get("quantity", row.get("qty", row.get("Quantity", row.get("count", 1))))
        if part in (None, "") or colour in (None, ""):
            raise ValueError(f"{where}: row {n} needs a part and a colour code")
        try:
            quantity = int(quantity)
        except (TypeError, ValueError):
            raise ValueError(f"{where}: row {n} has a bad quantity {quantity!r}") from None
        if quantity < 0:
            raise ValueError(f"{where}: row {n} has a negative quantity")
        colour = int(colour) if str(colour).strip().lstrip("-").isdigit() else str(colour).strip()
        counts[(_part_key(part), colour)] += quantity
    return +counts


def load_box(path, parts, instance_limit=100000):
    """(Counter of (part, colour) -> quantity, hidden count, description).

    Accepts another build (.mpd/.ldr or JSON plan), a saved box JSON
    (``parts-box``), a ``bom`` report JSON, or a CSV with part, colour and
    quantity columns."""
    path = Path(path)
    suffix = path.suffix.casefold()
    if suffix in {".mpd", ".ldr"}:
        model, overlay = load_model(path, parts, instance_limit)
        rows = bricks(model, overlay)
        return _counter(rows), sum(r["hidden"] for r in rows), "build"
    if suffix == ".csv":
        with path.open(newline="", encoding="utf-8-sig") as f:
            return _rows_counter(list(csv.DictReader(f)), path), 0, "parts list"
    if suffix == ".json":
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict) and data.get("format") == "nova-box":
            return _rows_counter(data.get("parts", []), path), 0, "box"
        if isinstance(data, dict) and isinstance(data.get("bom"), list):
            return _rows_counter(data["bom"], path), 0, "bom report"
        if isinstance(data, dict) and "sections" in data:
            model, overlay = load_model(path, parts, instance_limit)
            rows = bricks(model, overlay)
            return _counter(rows), sum(r["hidden"] for r in rows), "plan"
        if isinstance(data, list):
            return _rows_counter(data, path), 0, "parts list"
        raise ValueError(f"{path}: not a box, BOM report, parts list or plan")
    raise ValueError(f"{path}: expected .mpd, .ldr, .json or .csv")


def _box_rows(counter, parts=None):
    rows = []
    for (part, colour), quantity in sorted(counter.items(), key=lambda kv: (kv[0][0], str(kv[0][1]))):
        row = dict(part=part, colour=colour, quantity=quantity)
        if parts is not None:
            try:
                row["description"] = parts.description_for(part)
            except Exception:  # noqa: BLE001 - descriptions are optional
                pass
            if isinstance(colour, int):
                known = parts.colours_by_code.get(colour)
                if known is not None:
                    row["colour_name"] = known.name
        rows.append(row)
    return rows


def save_box(source, parts, output, instance_limit=100000):
    counter, _hidden, kind = load_box(source, parts, instance_limit)
    box = dict(format="nova-box", version=1, source=str(source), source_kind=kind,
               total=sum(counter.values()), parts=_box_rows(counter, parts))
    atomic_write(output, dumps(box) + "\n")
    return dict(output=str(output), total=box["total"], kinds=len(box["parts"]), source_kind=kind)


# ---------------------------------------------------------------------------
#  Matching a build against a box
# ---------------------------------------------------------------------------

def parts_match(build, box, parts, *, leftovers=0, hidden_percent=DEFAULT_HIDDEN_PERCENT,
                strict=False, instance_limit=100000):
    """Compare a build with the box. Passes when the build needs nothing the
    box lacks, at most ``leftovers`` box parts go unused, and hidden filler
    stays within ``hidden_percent`` of each build. ``strict`` means exact
    counts and no hidden filler."""
    model, overlay = load_model(build, parts, instance_limit)
    rows = bricks(model, overlay)
    used = _counter(rows)
    have, box_hidden, kind = load_box(box, parts, instance_limit)
    missing = used - have
    left = have - used
    total_used, total_box = sum(used.values()), sum(have.values())
    hidden = sum(r["hidden"] for r in rows)
    if strict:
        leftovers, hidden_percent = 0, 0.0
    limit = hidden_percent / 100.0
    problems = []
    if missing:
        problems.append(f"The build uses {sum(missing.values())} parts the box doesn't have")
    if sum(left.values()) > leftovers:
        problems.append(f"{sum(left.values())} box parts are unused (allowed: {leftovers})")
    if total_used and hidden > limit * total_used + 1e-9:
        problems.append(f"{hidden} hidden filler parts in the build, over {hidden_percent:g}%")
    if kind in {"build", "plan"} and total_box and box_hidden > limit * total_box + 1e-9:
        problems.append(f"{box_hidden} hidden filler parts in the box build, over {hidden_percent:g}%")
    exact = not missing and not left
    advice = []
    if missing:
        advice.append("Swap the missing parts for leftovers of the same size, or add them to the "
                      "other build in a section named '...-hidden.ldr' and rebuild the box.")
    if sum(left.values()) > leftovers:
        advice.append("Use the leftovers in this build, or tuck them inside it in a section "
                      "named '...-hidden.ldr' (attached, out of sight).")
    return dict(
        checks_passed=not problems, exact_match=exact, build=str(build), box=str(box), box_kind=kind,
        build_parts=total_used, box_parts=total_box,
        missing_count=sum(missing.values()), leftover_count=sum(left.values()),
        hidden_in_build=hidden, hidden_in_box=box_hidden if kind in {"build", "plan"} else None,
        allowed=dict(leftovers=leftovers, hidden_percent=hidden_percent, strict=strict),
        problems=problems, advice=advice,
        missing=_box_rows(missing, parts), leftovers=_box_rows(left, parts),
        remaining_note="'leftovers' is what is still left in the box for this build.")


# ---------------------------------------------------------------------------
#  Better Destruction pairing file
# ---------------------------------------------------------------------------

def _hungarian(cost):
    """Minimum-cost assignment for a rectangular cost matrix (rows <= cols).
    Returns the column for each row. O(n^2 m); fine for one part/colour group."""
    n, m = cost.shape
    inf = float("inf")
    u, v = np.zeros(n + 1), np.zeros(m + 1)
    p, way = np.zeros(m + 1, dtype=int), np.zeros(m + 1, dtype=int)
    for i in range(1, n + 1):
        p[0], j0 = i, 0
        minv = np.full(m + 1, inf)
        used = np.zeros(m + 1, dtype=bool)
        while True:
            used[j0] = True
            i0, delta, j1 = p[j0], inf, 0
            for j in range(1, m + 1):
                if not used[j]:
                    cur = cost[i0 - 1, j - 1] - u[i0] - v[j]
                    if cur < minv[j]:
                        minv[j], way[j] = cur, j0
                    if minv[j] < delta:
                        delta, j1 = minv[j], j
            for j in range(m + 1):
                if used[j]:
                    u[p[j]] += delta
                    v[j] -= delta
                else:
                    minv[j] -= delta
            j0 = j1
            if p[j0] == 0:
                break
        while True:
            j1 = way[j0]
            p[j0] = p[j1]
            j0 = j1
            if j0 == 0:
                break
    result = [-1] * n
    for j in range(1, m + 1):
        if p[j]:
            result[p[j] - 1] = j - 1
    return result


def assign_shortest(a, b, exact_limit=200):
    """Pairs (i, j) between point lists a and b with short total travel."""
    if not len(a) or not len(b):
        return []
    a, b = np.asarray(a, float), np.asarray(b, float)
    cost = np.linalg.norm(a[:, None, :] - b[None, :, :], axis=2)
    if min(len(a), len(b)) <= exact_limit:
        if len(a) <= len(b):
            return [(i, j) for i, j in enumerate(_hungarian(cost))]
        return [(i, j) for j, i in enumerate(_hungarian(cost.T))]
    pairs, free_a, free_b = [], set(range(len(a))), set(range(len(b)))
    for flat in np.argsort(cost, axis=None):
        i, j = divmod(int(flat), len(b))
        if i in free_a and j in free_b:
            pairs.append((i, j))
            free_a.discard(i)
            free_b.discard(j)
            if not free_a or not free_b:
                break
    return pairs


def _clean(values, digits=6):
    out = []
    for x in values:
        x = round(float(x), digits)
        out.append(int(x) if x == int(x) else x)
    return out


def _side(row):
    return dict(pos=_clean(row["pos"]), rot=_clean(row["rot"].reshape(-1)),
                center=_clean(row["center"], 3), hidden=bool(row["hidden"]))


def build_order(rows):
    """Bottom-up order: a brick never comes before the bricks under it.
    Sorted by the lowest point of each brick (largest Y, since -Y is up),
    then the model's own step, then front-to-back and left-to-right."""
    keys = sorted(range(len(rows)), key=lambda n: (-round(rows[n]["bottom"], 3), rows[n]["step"],
                                                   round(rows[n]["center"][2], 3), round(rows[n]["center"][0], 3)))
    order = [0] * len(rows)
    for rank, n in enumerate(keys):
        order[n] = rank
    return order


def pair_file(set1, set2, parts, output, *, name1=None, name2=None, instance_limit=100000):
    m1, p1 = load_model(set1, parts, instance_limit)
    m2, p2 = load_model(set2, parts, instance_limit)
    a, b = bricks(m1, p1), bricks(m2, p2)
    order2 = build_order(b)
    groups = {}
    for side, rows in (("a", a), ("b", b)):
        for n, row in enumerate(rows):
            groups.setdefault((row["part"], row["colour"]), {"a": [], "b": []})[side].append(n)
    entries = []
    for key in sorted(groups, key=lambda k: (k[0], str(k[1]))):
        ia, ib = groups[key]["a"], groups[key]["b"]
        pairs = assign_shortest([a[i]["center"] for i in ia], [b[j]["center"] for j in ib])
        done_a, done_b = {i for i, _ in pairs}, {j for _, j in pairs}
        for i, j in pairs:
            entries.append((a[ia[i]], b[ib[j]], order2[ib[j]]))
        entries += [(a[ia[i]], None, None) for i in range(len(ia)) if i not in done_a]
        entries += [(None, b[ib[j]], order2[ib[j]]) for j in range(len(ib)) if j not in done_b]
    # Bricks that end up in set 2 first, in build order; set 1 only last.
    entries.sort(key=lambda e: (e[2] is None, e[2] if e[2] is not None else 0,
                                (e[0] or e[1])["part"], str((e[0] or e[1])["colour"])))
    out_dir = Path(output).resolve().parent
    def rel(path):
        path = Path(path).resolve()
        try:
            return path.relative_to(out_dir).as_posix()
        except ValueError:
            return str(path)
    data = {
        "format": "bd-pair", "version": 1, "units": "ldu", "up": "-Y",
        "sets": {"1": {"name": name1 or Path(set1).stem, "file": rel(set1)},
                 "2": {"name": name2 or Path(set2).stem, "file": rel(set2)}},
        "bricks": [],
    }
    for n, (x, y, order) in enumerate(entries):
        row = x or y
        brick = {"id": n, "part": row["part"], "color": row["colour"],
                 "set1": _side(x) if x else None, "set2": _side(y) if y else None}
        if order is not None:
            brick["order"] = order
        brick["hidden"] = bool(y["hidden"]) if y else False
        data["bricks"].append(brick)
    check = check_pair_data(data, a, b)
    if check["problems"]:
        raise ValueError("Pairing file failed its own check: " + "; ".join(check["problems"]))
    atomic_write(output, _pair_text(data))
    only1 = sum(1 for e in data["bricks"] if e["set2"] is None)
    only2 = sum(1 for e in data["bricks"] if e["set1"] is None)
    return dict(checks_passed=True, output=str(output), bricks=len(data["bricks"]),
                paired=len(data["bricks"]) - only1 - only2, only_in_set1=only1, only_in_set2=only2,
                hidden_in_set1=sum(1 for r in a if r["hidden"]), hidden_in_set2=sum(1 for r in b if r["hidden"]),
                exact_match=not only1 and not only2,
                note="Bricks only in one set get null for the other set." if only1 or only2 else
                     "Every brick is used in both sets.")


def _pair_text(data):
    """Readable JSON with one line per brick."""
    head = {k: v for k, v in data.items() if k != "bricks"}
    text = json.dumps(head, indent=2)[:-2]
    lines = [json.dumps(b, separators=(", ", ": ")) for b in data["bricks"]]
    return text + ',\n  "bricks": [\n    ' + ",\n    ".join(lines) + "\n  ]\n}\n"


def check_pair_data(data, rows1, rows2):
    """The rules from the bd-pair spec: unique ids, both sides list the same
    (part, colour) multiset apart from nulls, and every set's bricks appear
    exactly once."""
    problems = []
    ids = [b["id"] for b in data["bricks"]]
    if len(ids) != len(set(ids)):
        problems.append("brick ids are not unique")
    for side, rows in (("set1", rows1), ("set2", rows2)):
        listed = Counter((b["part"], b["color"]) for b in data["bricks"] if b[side])
        if listed != _counter(rows):
            problems.append(f"{side} bricks don't match its model")
    for b in data["bricks"]:
        if "order" in b and b["set2"] is None:
            problems.append(f"brick {b['id']} has an order but no set 2 place")
            break
    return dict(problems=problems)
