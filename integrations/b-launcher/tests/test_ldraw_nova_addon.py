"""Tests for the B-Launcher add-on against a fake LDRAW-NOVA web app.

Run: python -m pytest integrations/b-launcher/tests -q
"""
import json
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

ADDON = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "ldraw_nova")
sys.path.insert(0, ADDON)

import main  # noqa: E402
from blauncher_addon import JobStopped  # noqa: E402


class FakeWeb:
    """Just enough of the web app: models, chats, a turn that makes a model."""

    def __init__(self, make_model=True, error=None, slow=0.0):
        self.make_model, self.error, self.slow = make_model, error, slow
        self.chats, self.sent, self.cancelled = {}, [], []
        self.files = {"/files/generated/castle-v1.mpd": b"0 FILE castle.mpd\n",
                      "/files/generated/castle-v1.png": b"\x89PNG fake",
                      "/files/generated/castle-v1.bom.json": b"{}"}
        web = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, code, body, kind="application/json"):
                data = body if isinstance(body, bytes) else json.dumps(body).encode()
                self.send_response(code)
                self.send_header("Content-Type", kind)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def _body(self):
                n = int(self.headers.get("Content-Length") or 0)
                return json.loads(self.rfile.read(n) or b"{}")

            def do_GET(self):
                p = self.path
                if p == "/api/llm-models":
                    return self._send(200, {"models": [
                        {"id": "c1", "model_name": "Claude Sonnet 5"},
                        {"id": "g1", "model_name": "GPT-5"}], "default_id": "c1"})
                if p.startswith("/files/"):
                    return self._send(200, web.files[p], "application/octet-stream") \
                        if p in web.files else self._send(404, {"detail": "nope"})
                if p.startswith("/api/chats/") and p.endswith("/stream"):
                    chat_id = p.split("/")[3]
                    self.send_response(200)
                    self.send_header("Content-Type", "text/event-stream")
                    self.end_headers()
                    events = [("snapshot", {"running": True}),
                              ("progress", {"summary": "Agent is reviewing the request."}),
                              ("text", {"delta": "I'll build a "}),
                              ("text", {"delta": "small castle.\nTwo towers"}),
                              ("tool_start", {"name": "build", "arguments":
                                              json.dumps({"command": "ldraw-agent build castle.plan.json"})}),
                              ("tool_output", {"channel": "stdout", "delta": "wrote castle.mpd\n"})]
                    for ev, data in events:
                        self.wfile.write(f"event: {ev}\ndata: {json.dumps(data)}\n\n".encode())
                        self.wfile.flush()
                    deadline = time.time() + web.slow
                    while time.time() < deadline and chat_id not in web.cancelled:
                        self.wfile.write(b"event: activity\ndata: {}\n\n")
                        self.wfile.flush()
                        time.sleep(0.05)
                    web.finish(chat_id)
                    if web.error:
                        self.wfile.write(f"event: turn_error\ndata: {json.dumps({'message': web.error})}\n\n".encode())
                    self.wfile.write(b"event: done\ndata: {}\n\n")
                    return
                if p.startswith("/api/chats/"):
                    chat = web.chats.get(p.split("/")[3])
                    if not chat:
                        return self._send(404, {"detail": "no such chat"})
                    return self._send(200, {"chat": {"id": chat["id"], "running": False},
                                            "messages": chat["messages"], "models": chat["models"]})
                self._send(404, {"detail": "not found"})

            def do_POST(self):
                p, body = self.path, self._body()
                if p == "/api/chats":
                    chat_id = f"chat{len(web.chats) + 1}"
                    web.chats[chat_id] = {"id": chat_id, "models": {}, "messages": [],
                                          "llm": body.get("llm_model_id")}
                    return self._send(200, {"id": chat_id})
                if p.endswith("/messages"):
                    # As the real web app (model_catalog.validate_options).
                    options = body.get("options") or {}
                    if options.get("mode", "agent") not in ("plan", "agent"):
                        return self._send(400, {"detail": "Unknown mode"})
                    if options.get("permissions", "ask") not in ("ask", "full", "read_only"):
                        return self._send(400, {"detail": "Unknown permission setting"})
                    web.sent.append((p.split("/")[3], body))
                    return self._send(202, {"started": True})
                if p.endswith("/cancel"):
                    web.cancelled.append(p.split("/")[3])
                    return self._send(200, {"cancelled": True})
                self._send(404, {"detail": "not found"})

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.server.server_port}"

    def finish(self, chat_id):
        chat = self.chats[chat_id]
        chat["messages"].append({"role": "assistant", "content": "Here is the plan."})
        last = next((body for c, body in reversed(self.sent) if c == chat_id), {})
        if (last.get("options") or {}).get("mode") == "plan":
            return                      # a plan or reference-only message builds nothing
        if self.make_model and chat_id not in self.cancelled:
            chat["models"][f"m{len(chat['models'])}"] = {
                "id": f"m{len(chat['models'])}", "file": "castle-v1.mpd", "parts": 42,
                "model_url": "/files/generated/castle-v1.mpd",
                "image_url": "/files/generated/castle-v1.png?v=1",
                "bom_url": "/files/generated/castle-v1.bom.json?v=1",
                "status": "ready", "bom_status": "ready", "created_at": time.time()}


class FakeAddon:
    def __init__(self):
        self.notes, self.stop = [], set()

    def job_note(self, job_id, text):
        self.notes.append(text)

    def notify(self, *a):
        pass

    def cancelled(self, job_id):
        return "cancelled" if job_id in self.stop else ""


def job(**fields):
    base = {"prompt": "a small castle", "ai": "Default", "mode": "Build",
            "chat": "New chat", "output": "", "extras": "yes"}
    return {"id": "j1", "node_kind": "lego_build", "board": "b1", "node": "n1",
            "fields": {**base, **fields}, "inputs": {}}


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "STATE_FILE", str(tmp_path / "state.json"))

    def make(**kw):
        web = FakeWeb(**kw)
        addon = FakeAddon()
        builder = main.Builder(addon, {"web_url": web.url, "folder": str(tmp_path / "out")})
        return web, addon, builder
    yield make


def test_build_saves_mpd_and_extras(setup, tmp_path):
    web, addon, builder = setup()
    result = builder.run(job(ai="GPT-5"))
    out = tmp_path / "out" / "castle-v1.mpd"
    assert result["output"] == str(out)
    assert out.read_bytes().startswith(b"0 FILE")
    assert (tmp_path / "out" / "castle-v1.png").exists()
    assert (tmp_path / "out" / "castle-v1.bom.json").exists()
    chat_id, body = web.sent[0]
    assert body["llm_model_id"] == "g1"
    assert body["options"] == {"mode": "agent", "permissions": "full"}


def test_save_as_path_and_versions(setup, tmp_path):
    web, addon, builder = setup()
    target = tmp_path / "lego" / "tower.mpd"
    first = builder.run(job(output=str(target), extras="no"))["output"]
    second = builder.run(job(output=str(target), extras="no"))["output"]
    assert first == str(target)
    assert second == str(tmp_path / "lego" / "tower-v2.mpd")
    assert not (tmp_path / "lego" / "tower.png").exists()


def test_continue_chat_reuses_the_nodes_chat(setup):
    web, addon, builder = setup()
    builder.run(job())
    builder.run(job(chat="Continue this node's chat"))
    assert [c for c, _ in web.sent] == ["chat1", "chat1"]
    builder.run(job())
    assert web.sent[-1][0] == "chat2"


def test_no_model_fails_with_the_ai_error(setup):
    web, addon, builder = setup(make_model=False, error="model refused")
    with pytest.raises(RuntimeError, match="model refused"):
        builder.run(job())


def test_plan_only_needs_no_model(setup):
    web, addon, builder = setup(make_model=False)
    result = builder.run(job(mode="Plan only"))
    assert "Plan" in result["message"]
    assert web.sent[0][1]["options"]["mode"] == "plan"


def test_cancel_stops_the_ai(setup):
    web, addon, builder = setup(slow=5)
    threading.Timer(0.3, lambda: addon.stop.add("j1")).start()
    with pytest.raises(JobStopped):
        builder.run(job())
    assert web.cancelled == ["chat1"]


def test_unknown_ai_and_missing_prompt(setup):
    web, addon, builder = setup()
    with pytest.raises(RuntimeError, match="isn't set up"):
        builder.run(job(ai="Llama"))
    with pytest.raises(JobStopped, match="no prompt"):
        builder.run(job(prompt="  "))


def test_web_app_down_is_a_clear_error(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "STATE_FILE", str(tmp_path / "state.json"))
    builder = main.Builder(FakeAddon(), {"web_url": "http://127.0.0.1:9"})
    with pytest.raises(main.NovaError, match="Is the web app running"):
        builder.run(job())


def test_ai_dropdown_is_filled_from_the_web_app():
    kinds = main.kinds_with_ai(["Claude Sonnet 5", "GPT-5", "GPT-5"])
    ai = next(f for f in kinds[0]["fields"] if f["key"] == "ai")
    assert ai["options"] == ["Default", "Claude Sonnet 5", "GPT-5"]


def test_long_or_duplicate_ai_names_stay_valid():
    long = "X" * 90
    kinds = main.kinds_with_ai([long, long[:85], "Default", " "])
    ai = next(f for f in kinds[0]["fields"] if f["key"] == "ai")
    assert ai["options"] == ["Default", "X" * 80]


def test_a_dropped_stream_reconnects(setup, monkeypatch):
    web, addon, builder = setup()
    real = builder.nova.stream
    calls = []

    def flaky(chat_id, timeout=30):
        calls.append(1)
        if len(calls) == 1:
            web.finish(chat_id)       # the build ends while we're cut off
            raise main.NovaError("The progress stream dropped (timed out).")
        yield from real(chat_id)
    monkeypatch.setattr(builder.nova, "stream", flaky)
    monkeypatch.setattr(main.time, "sleep", lambda s: None)
    assert builder.run(job())["output"].endswith("castle-v1.mpd")
    assert len(calls) == 1          # not running any more: no reconnect needed


def test_stream_stall_becomes_nova_error():
    import socket
    from nova_client import Nova, NovaError

    class Stalled:
        def __iter__(self):
            return self
        def __next__(self):
            raise socket.timeout("timed out")
        def __enter__(self):
            return self
        def __exit__(self, *a):
            pass
    nova = Nova("http://x")
    nova._open = lambda *a, **k: Stalled()
    with pytest.raises(NovaError, match="dropped"):
        list(nova.stream("c"))


class ShowingAddon(FakeAddon):
    def __init__(self, fail=False):
        super().__init__()
        self.previews, self.references, self.fail = [], [], fail

    def job_preview(self, job_id, image):
        if self.fail:
            raise RuntimeError("B-Launcher is too old")
        self.previews.append((job_id, image))

    def job_reference(self, job_id, image, caption="", title="", group=""):
        self.references.append((job_id, image, caption, title, group))


def showing(tmp_path, monkeypatch, fail=False):
    monkeypatch.setattr(main, "STATE_FILE", str(tmp_path / "state.json"))
    web, addon = FakeWeb(), ShowingAddon(fail)
    return web, addon, main.Builder(addon, {"web_url": web.url,
                                            "folder": str(tmp_path / "out")})


def test_the_finished_picture_goes_in_the_node_by_default(tmp_path, monkeypatch):
    web, addon, builder = showing(tmp_path, monkeypatch)
    builder.run(job(extras="no"))
    assert addon.previews == [("j1", b"\x89PNG fake")]
    assert addon.references == []
    assert not (tmp_path / "out" / "castle-v1.png").exists()


def test_the_finished_picture_on_the_board_or_both(tmp_path, monkeypatch):
    web, addon, builder = showing(tmp_path, monkeypatch)
    builder.run(job(show=main.SHOW_GROUP))
    assert addon.previews == []
    (_, image, caption, title, group), = addon.references
    assert image == b"\x89PNG fake" and title == "castle-v1.mpd"
    assert caption == "castle-v1.mpd (42 parts)" and group == main.RESULTS_GROUP
    builder.run(job(show=main.SHOW_BOTH))
    assert len(addon.previews) == 1 and len(addon.references) == 2
    builder.run(job(show=main.SHOW_OFF))
    assert len(addon.previews) == 1 and len(addon.references) == 2


def test_showing_the_picture_never_fails_the_build(tmp_path, monkeypatch):
    web, addon, builder = showing(tmp_path, monkeypatch, fail=True)
    assert builder.run(job())["output"].endswith("castle-v1.mpd")
    assert any("Couldn't show the picture" in n for n in addon.notes)


class ConsoleAddon(FakeAddon):
    def __init__(self, broken=False):
        super().__init__()
        self.console, self.broken = [], broken

    def job_console(self, job_id, text):
        if self.broken:
            raise RuntimeError("404 not found")
        self.console.extend(text.split("\n"))


def test_the_chat_shows_in_the_nodes_console(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "STATE_FILE", str(tmp_path / "state.json"))
    web, addon = FakeWeb(), ConsoleAddon()
    builder = main.Builder(addon, {"web_url": web.url, "folder": str(tmp_path / "out")})
    builder.run(job(extras="no"))
    assert addon.console == ["… Agent is reviewing the request.",
                             "NOVA:", "I'll build a small castle.", "Two towers",
                             "> build: ldraw-agent build castle.plan.json",
                             "wrote castle.mpd"]
    assert "Agent is reviewing the request." in addon.notes   # progress note fixed


def test_an_old_blauncher_without_a_console_still_builds(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "STATE_FILE", str(tmp_path / "state.json"))
    web, addon = FakeWeb(), ConsoleAddon(broken=True)
    builder = main.Builder(addon, {"web_url": web.url, "folder": str(tmp_path / "out")})
    assert builder.run(job(extras="no"))["output"].endswith("castle-v1.mpd")


def test_tool_lines():
    assert main.tool_line({"name": "read_file", "arguments": '{"path": "a.json"}'}) == "> read_file: a.json"
    assert main.tool_line({"name": "x", "arguments": "not json"}) == "> x"
