"""Group references: photos, notes and colours wired into the refs input."""
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "ldraw_nova"))

import main  # noqa: E402
import refs  # noqa: E402
from blauncher_addon import JobStopped  # noqa: E402
from test_ldraw_nova_addon import FakeAddon, FakeWeb, job  # noqa: E402

PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 20
JPEG = b"\xff\xd8\xff" + b"1" * 20


def node(id, kind, x=0, y=0, title="", group="", **extra):
    return {"id": id, "kind": kind, "x": x, "y": y, "title": title, "group": group,
            "fields": extra.pop("fields", {}), **extra}


def board(n_photos=2, notes=(("", "A red castle with two towers"),), colours=(),
          wire=("g1",)):
    nodes = [node("lego", "addon.ldraw_nova.lego_build", 900, 0),
             node("g1", "frame", 0, 0, fields={"title": "Castle gate"}),
             node("sk", "sketch", 5, 5, group="g1")]
    for i in range(n_photos):
        nodes.append(node(f"p{i}", "image", x=10 + i, y=50, group="g1", image=True,
                          caption=f"view {i}"))
    for i, (title, text) in enumerate(notes):
        nodes.append(node(f"n{i}", "note", x=i, y=10 + i, title=title, group="g1",
                          fields={"text": text}))
    if colours:
        nodes.append(node("c", "palette", y=90, group="g1", colours=list(colours)))
    members = [n["id"] for n in nodes if n.get("group") == "g1"]
    return {"id": "b1", "nodes": nodes,
            "groups": [{"id": "g1", "title": "Castle gate", "parent": "", "members": members}],
            "links": [{"from": w, "from_port": "photos", "to": "lego", "to_port": "refs"}
                      for w in wire]}


class FakeBLauncher(FakeAddon):
    """FakeAddon plus B-Launcher's board routes, served over HTTP for pictures."""

    def __init__(self, the_board, pictures):
        super().__init__()
        self.board, self.token, self.fetched = the_board, "t", []
        me = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                assert self.headers["Authorization"] == "Bearer t"
                q = dict(p.split("=") for p in self.path.split("?")[1].split("&"))
                me.fetched.append(q["node"])
                data = pictures.get(q["node"], b"")
                self.send_response(200 if data else 404)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
        srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{srv.server_port}"

    def get(self, path):
        assert path == "/board?id=b1"
        return self.board


@pytest.fixture
def run(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "STATE_FILE", str(tmp_path / "state.json"))

    def go(the_board, pictures=None, **fields):
        web = FakeWeb()
        addon = FakeBLauncher(the_board, pictures or {f"p{i}": PNG for i in range(20)})
        builder = main.Builder(addon, {"web_url": web.url, "folder": str(tmp_path / "out")})
        j = job(**{"prompt": "", **fields})
        j["node"] = "lego"
        return web, addon, builder.run(j)
    return go


def test_notes_become_the_prompt_and_photos_go_with_it(run, tmp_path):
    web, addon, result = run(board())
    chat_id, body = web.sent[-1]
    assert body["text"].startswith("A red castle with two towers")
    assert "Photo 1: view 0" in body["text"]
    assert len(body["images"]) == 2 and body["images"][0].startswith("data:image/png;base64,")
    # sketches are left out; the group title names the file
    assert result["output"] == str(tmp_path / "out" / "castle-gate.mpd")
    assert any("1 note(s)" in n and "2 photo(s)" in n for n in addon.notes)


def test_node_prompt_wins_over_notes(run):
    web, addon, _ = run(board(), prompt="A tiny house")
    text = web.sent[-1][1]["text"]
    assert text.startswith("A tiny house") and "red castle" not in text


def test_add_notes_to_my_prompt(run):
    web, addon, _ = run(board(), prompt="A tiny house", notes=refs.NOTES_ADD)
    text = web.sent[-1][1]["text"]
    assert text.startswith("A tiny house") and "More detail from the board" in text \
        and "red castle" in text


def test_ignore_notes(run):
    with pytest.raises(JobStopped, match="Nothing to build"):
        run(board(), notes=refs.NOTES_IGNORE)


def test_several_notes_merge_in_board_order_with_special_titles(run):
    notes = [("Walls", "Grey stone walls"), ("Avoid", "No minifigs"),
             ("", "Add a drawbridge"), ("Colours", "Mostly tan")]
    web, addon, _ = run(board(n_photos=0, notes=notes))
    text = web.sent[-1][1]["text"]
    assert text.index("Grey stone walls") < text.index("Add a drawbridge")
    assert "Walls:\nGrey stone walls" in text
    assert "Avoid:\nNo minifigs" in text and "Colour rules:\nMostly tan" in text
    assert text.index("Add a drawbridge") < text.index("Avoid:")


def test_colours_match_ldraw_names(run):
    web, addon, _ = run(board(colours=["#720012", "#e4cd9e", "#1b2a34"]))
    text = web.sent[-1][1]["text"]
    assert "Dark Red (320)" in text and "Tan (19)" in text and "Black (0)" in text


def test_nearest_colour():
    assert refs.nearest_ldraw("#701010") == (320, "Dark Red")
    assert refs.nearest_ldraw("#ffffff")[1] == "White"
    assert refs.nearest_ldraw("not a colour") is None


def test_more_than_four_photos_go_in_batches(run):
    web, addon, _ = run(board(n_photos=6))
    assert [len(b["images"]) for _, b in web.sent] == [4, 2]
    assert web.sent[0][1]["options"] == {"mode": "chat"}
    assert "Reference photos 1-4 of 6" in web.sent[0][1]["text"]
    assert web.sent[1][1]["options"]["mode"] == "agent"


def test_bad_and_too_many_photos_are_left_out_and_logged(run):
    pictures = {f"p{i}": PNG for i in range(20)}
    pictures["p0"] = b"GIF89a-not-allowed"
    web, addon, _ = run(board(n_photos=18), pictures)
    sent = sum(len(b["images"]) for _, b in web.sent)
    assert sent == refs.MAX_PHOTOS
    assert any("not a PNG" in n for n in addon.notes)
    assert any("more than 16" in n for n in addon.notes)


def test_nested_groups_and_a_reference_wired_alongside(run):
    b = board(n_photos=1)
    b["nodes"].append(node("g2", "frame", 0, 200, group="g1", fields={"title": "Inner"}))
    b["nodes"].append(node("p9", "image", 0, 210, group="g2", image=True, caption="inner"))
    b["nodes"].append(node("solo", "image", 500, 0, image=True, caption="solo"))
    b["groups"][0]["members"].append("g2")
    b["groups"].append({"id": "g2", "title": "Inner", "parent": "g1", "members": ["p9"]})
    b["links"].append({"from": "solo", "from_port": "out", "to": "lego", "to_port": "refs"})
    web, addon, _ = run(b, {"p0": PNG, "p9": JPEG, "solo": PNG})
    text = web.sent[-1][1]["text"]
    assert "Photo 2: inner" in text and "Photo 3: solo" in text
    assert web.sent[-1][1]["images"][1].startswith("data:image/jpeg")


def test_photo_paths_without_the_board(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "STATE_FILE", str(tmp_path / "state.json"))
    pic = tmp_path / "a.png"
    pic.write_bytes(PNG)
    web = FakeWeb()
    builder = main.Builder(FakeAddon(), {"web_url": web.url, "folder": str(tmp_path)})
    j = job(prompt="castle")
    j["inputs"] = {"refs": [str(pic), str(tmp_path / "missing.png")]}
    builder.run(j)
    assert len(web.sent[-1][1]["images"]) == 1


def test_in_input_text_is_a_prompt_but_refs_list_is_not(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "STATE_FILE", str(tmp_path / "state.json"))
    web = FakeWeb()
    builder = main.Builder(FakeAddon(), {"web_url": web.url, "folder": str(tmp_path)})
    j = job(prompt="")
    j["inputs"] = {"refs": [], "in": "a boat"}
    builder.run(j)
    assert web.sent[-1][1]["text"].startswith("a boat")


def test_settings_live_outside_the_addon_folder(monkeypatch, tmp_path):
    monkeypatch.delenv("LDRAW_NOVA_ADDON_DATA", raising=False)
    monkeypatch.setattr(main.os, "name", "nt")
    monkeypatch.setenv("APPDATA", str(tmp_path))
    assert main.data_dir() == os.path.join(str(tmp_path), "LDRAW-NOVA addon")
