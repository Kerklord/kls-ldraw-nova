import json

import pytest

from ldraw_tools import multiset
from conftest import IDENTITY, mpd, ref

TURN = "0 0 1 0 1 0 -1 0 0"   # 90 degrees about Y


def write(path, text):
    path.write_text(text, encoding="utf-8", newline="")
    return path


def two_file(main_body, sub_name, sub_body, name="main.ldr"):
    main = mpd(main_body, name).replace("\r\n", "\n")
    sub = mpd(sub_body, sub_name).replace("\r\n", "\n")
    return (main + sub).replace("\n", "\r\n")


@pytest.fixture
def builds(tmp_path):
    # Speaker: two red bricks stacked on a blue plate, one yellow plate on top.
    speaker = write(tmp_path / "speaker.mpd", mpd("\n".join([
        ref("3022.dat", 1, "0 0 0"),
        ref("3001.dat", 4, "0 -24 0"),
        ref("3001.dat", 4, "0 -48 0"),
        ref("3022.dat", 14, "0 -56 0"),
    ]), "speaker.ldr"))
    # Stage: the same parts side by side, the yellow plate tucked in a hidden section.
    stage = write(tmp_path / "stage.mpd", two_file("\n".join([
        ref("3022.dat", 1, "0 0 0"),
        ref("3001.dat", 4, "-20 -24 0", TURN),
        ref("3001.dat", 4, "20 -24 0", TURN),
        ref("stage-hidden-filler.ldr", 16, "0 0 0"),
    ]), "stage-hidden-filler.ldr", ref("3022.dat", 14, "100 0 0"), "stage.ldr"))
    return speaker, stage


def test_box_and_exact_match(parts, builds, tmp_path):
    speaker, stage = builds
    saved = multiset.save_box(speaker, parts, tmp_path / "box.json")
    assert saved["total"] == 4
    report = multiset.parts_match(stage, tmp_path / "box.json", parts, hidden_percent=25)
    assert report["checks_passed"] and report["exact_match"]
    assert report["hidden_in_build"] == 1


def test_hidden_cap_and_strict(parts, builds):
    speaker, stage = builds
    # One hidden part out of four is 25%: over the 10% default.
    assert not multiset.parts_match(stage, speaker, parts, hidden_percent=10)["checks_passed"]
    assert multiset.parts_match(stage, speaker, parts, hidden_percent=25)["checks_passed"]
    assert not multiset.parts_match(stage, speaker, parts, hidden_percent=25, strict=True)["checks_passed"]


def test_missing_and_leftovers(parts, builds, tmp_path):
    speaker, _stage = builds
    other = write(tmp_path / "other.mpd", mpd("\n".join([
        ref("3022.dat", 1, "0 0 0"),
        ref("3001.dat", 4, "0 -24 0"),
        ref("3003.dat", 0, "0 -48 0"),
    ]), "other.ldr"))
    report = multiset.parts_match(other, speaker, parts, leftovers=5, hidden_percent=0)
    assert not report["checks_passed"]
    assert report["missing"] == [dict(part="3003", colour=0, quantity=1,
                                      description="Brick 2 x 2", colour_name="Black")]
    assert {(r["part"], r["colour"]) for r in report["leftovers"]} == {("3001", 4), ("3022", 14)}


def test_csv_box(parts, builds, tmp_path):
    speaker, _ = builds
    box = tmp_path / "set.csv"
    box.write_text("part,colour,quantity\n3001.dat,4,2\n3022,1,1\n3022,14,1\n")
    assert multiset.parts_match(speaker, box, parts, hidden_percent=0)["exact_match"]


def test_pair_file(parts, builds, tmp_path):
    speaker, stage = builds
    out = tmp_path / "speaker-stage.bdpair.json"
    report = multiset.pair_file(speaker, stage, parts, out, name1="Speaker", name2="Stage")
    assert report["exact_match"] and report["bricks"] == 4
    data = json.loads(out.read_text())
    assert data["format"] == "bd-pair" and data["units"] == "ldu" and data["up"] == "-Y"
    assert data["sets"]["1"] == {"name": "Speaker", "file": "speaker.mpd"}
    ids = [b["id"] for b in data["bricks"]]
    assert sorted(ids) == list(range(4))
    by_part = {(b["part"], b["color"]): b for b in data["bricks"] if b["part"] == "3022"}
    hidden = by_part[("3022", 14)]
    assert hidden["hidden"] is True and hidden["set2"]["pos"] == [100, 0, 0]
    assert hidden["set1"]["pos"] == [0, -56, 0]
    reds = [b for b in data["bricks"] if b["part"] == "3001"]
    assert all(b["set2"]["rot"] == [0, 0, 1, 0, 1, 0, -1, 0, 0] for b in reds)
    # Body centre of a 2x4 brick sits 12 LDU below its origin.
    assert sorted(b["set1"]["center"][1] for b in reds) == [-36, -12]
    # The blue base plate goes first; nothing comes before what it sits on.
    orders = {(b["part"], b["color"], tuple(b["set2"]["pos"])): b["order"] for b in data["bricks"]}
    assert orders[("3022", 1, (0, 0, 0))] in (0, 1)
    assert sorted(orders.values()) == [0, 1, 2, 3]


def test_pair_file_marks_one_sided(parts, builds, tmp_path):
    speaker, _ = builds
    small = write(tmp_path / "small.mpd", mpd(ref("3022.dat", 1, "0 0 0"), "small.ldr"))
    report = multiset.pair_file(speaker, small, parts, tmp_path / "x.bdpair.json")
    assert report["only_in_set1"] == 3 and report["paired"] == 1
    data = json.loads((tmp_path / "x.bdpair.json").read_text())
    assert all("order" not in b for b in data["bricks"] if b["set2"] is None)


def test_shortest_pairing():
    a = [[0, 0, 0], [100, 0, 0]]
    b = [[101, 0, 0], [1, 0, 0]]
    assert sorted(multiset.assign_shortest(a, b)) == [(0, 1), (1, 0)]
    assert len(multiset.assign_shortest(a, b[:1])) == 1
