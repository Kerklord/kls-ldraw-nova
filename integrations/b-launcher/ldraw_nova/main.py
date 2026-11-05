"""LDRAW-NOVA add-on for B-Launcher.

Adds a "LEGO build" node to the board. When a board reaches it, the add-on
asks the LDRAW-NOVA web app to build the prompt, follows the build, then
saves the finished .mpd (and, if asked, its snapshot and parts list) where
the node says, and hands that file to the next node (for example Open in
program, to open it in Stud.io).

The panel (Tools tab) holds the web app's address and the default folder,
and fills the node's AI dropdown with the models set up in the web app.
"""
import json
import os
import threading
import time
import urllib.parse

import refs as references
from nova_client import Nova, NovaError

HERE = os.path.dirname(os.path.abspath(__file__))
ADDON_DIR = os.environ.get("BLAUNCHER_ADDON_DIR") or HERE


SHOW_NODE = "In the node"
SHOW_GROUP = "On the board, in a group"
SHOW_BOTH = "Both"
SHOW_OFF = "Don't show it"
RESULTS_GROUP = "LEGO builds"
REFERENCE_OPTIONS = {"mode": "plan", "permissions": "read_only"}


def data_dir():
    """Where settings live: outside the add-on's folder, because B-Launcher
    deletes that folder when the add-on is reinstalled."""
    if os.environ.get("LDRAW_NOVA_ADDON_DATA"):
        return os.environ["LDRAW_NOVA_ADDON_DATA"]
    if os.name == "nt" and os.environ.get("APPDATA"):
        return os.path.join(os.environ["APPDATA"], "LDRAW-NOVA addon")
    return os.path.join(os.path.expanduser("~"), ".config", "ldraw-nova-addon")


DATA_DIR = data_dir()
SETTINGS_FILE = os.path.join(DATA_DIR, "settings.json")
STATE_FILE = os.path.join(DATA_DIR, "state.json")

DEFAULT_SETTINGS = {
    "web_url": "http://localhost:8765",
    "folder": os.path.join(os.path.expanduser("~"), "Documents", "LDRAW-NOVA"),
}
AI_DEFAULT = "Default"
CHAT_NEW = "New chat"
CHAT_CONTINUE = "Continue this node's chat"
MODE_PLAN = "Plan only"
NOTE_EVERY = 10          # seconds between progress notes in the queue log
STREAM_RETRIES = 5       # reconnects if the progress stream drops mid-build
CONSOLE_EVERY = 1.0      # seconds between batches sent to the node's console


# --- small helpers ------------------------------------------------------------

def load_json(path, default):
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
        return data if isinstance(data, type(default)) else default
    except (OSError, ValueError):
        return default


def save_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    partial = path + ".part"
    with open(partial, "w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2)
    os.replace(partial, path)


def truthy(value):
    return str(value).strip().lower() in ("1", "yes", "true", "on")


def free_path(path):
    """`path`, or castle-v2.mpd, castle-v3.mpd ... if it is taken."""
    if not os.path.exists(path):
        return path
    stem, ext = os.path.splitext(path)
    n = 2
    while os.path.exists(f"{stem}-v{n}{ext}"):
        n += 1
    return f"{stem}-v{n}{ext}"


def target_path(output_field, folder, model_file, title=""):
    """Where to save the model: the node's Save as, else the default folder.
    A group's title, when there is one, names the file in a folder."""
    name = references.slug(title)
    if name:
        model_file = name + (os.path.splitext(model_file)[1] or ".mpd")
    output = (output_field or "").strip().strip('"')
    if output:
        if os.path.isdir(output) or output.endswith(("/", "\\")):
            path = os.path.join(output, model_file)
        elif os.path.splitext(output)[1].lower() in (".mpd", ".ldr"):
            path = output
        else:
            path = output + ".mpd"
    else:
        path = os.path.join(folder or DEFAULT_SETTINGS["folder"], model_file)
    return free_path(os.path.abspath(path))


def url_name(url):
    return urllib.parse.unquote(os.path.basename((url or "").split("?", 1)[0]))


def describe(event, data):
    """A queue-log line for a progress event, or None for ones not worth one."""
    if event == "tool_start":
        name = data.get("name") or "a tool"
        return f"Running {name}"
    if event == "progress":
        return data.get("summary") or data.get("message") or data.get("phase")
    if event == "activity":
        return data.get("phase")
    return None


def tool_line(data):
    """'> build castle.plan.json' for a tool the AI started."""
    name = data.get("name") or "a tool"
    try:
        args = json.loads(data.get("arguments") or "{}")
    except (TypeError, ValueError):
        args = {}
    detail = ""
    if isinstance(args, dict):
        for key in ("command", "cmd", "path", "file", "query", "summary"):
            if isinstance(args.get(key), str) and args[key].strip():
                detail = args[key].strip().splitlines()[0]
                break
    return f"> {name}" + (f": {detail[:200]}" if detail else "")


class ConsoleFeed:
    """The LDRAW-NOVA chat, live, in the console of the node's panel.

    The AI's reply arrives a few words at a time and tool output a chunk at
    a time; both are gathered into whole lines and sent about once a second.
    A B-Launcher too old to have a console just doesn't get one."""

    def __init__(self, addon, job_id):
        self.addon, self.job_id = addon, job_id
        self.ok = hasattr(addon, "job_console")
        self.lines, self.partial, self.kind, self.sent_at = [], "", "", 0.0

    def event(self, event, data):
        if not self.ok:
            return
        if event == "text":
            self._stream("reply", data.get("delta") or "")
        elif event == "tool_output":
            self._stream("output", data.get("delta") or "")
        elif event == "tool_start":
            self.line(tool_line(data))
        elif event == "progress":
            text = data.get("summary") or data.get("message")
            if text:
                self.line("… " + text)
        elif event == "turn_error":
            self.line("Error: " + (data.get("message") or "the AI reported an error"))
        self.flush()

    def line(self, text):
        self._end_partial()
        self.lines.append(text)

    def _stream(self, kind, text):
        if kind != self.kind:
            self._end_partial()
            self.kind = kind
            if kind == "reply":
                self.lines.append("NOVA:")
        *done, self.partial = (self.partial + text).split("\n")
        self.lines.extend(line for line in done if line.strip())

    def _end_partial(self):
        if self.partial.strip():
            self.lines.append(self.partial)
        self.partial, self.kind = "", ""

    def flush(self, force=False):
        if not self.ok or not (self.lines or (force and self.partial)):
            return
        if not force and time.time() - self.sent_at < CONSOLE_EVERY:
            return
        if force:
            self._end_partial()
        batch, self.lines = self.lines, []
        self.sent_at = time.time()
        try:
            self.addon.job_console(self.job_id, "\n".join(batch))
        except Exception:
            self.ok = False         # an older B-Launcher: carry on without


# --- the job ----------------------------------------------------------------

class Builder:
    """Runs LEGO build jobs. `addon` is the B-Launcher client (or a stand-in)."""

    def __init__(self, addon, settings=None, state=None, nova=None):
        self.addon = addon
        self.settings = {**DEFAULT_SETTINGS, **(settings or {})}
        self.state = state if state is not None else {"chats": {}}
        self.state.setdefault("chats", {})
        self.nova = nova or Nova(self.settings["web_url"])
        self.lock = threading.Lock()

    def save_state(self):
        with self.lock:
            try:
                save_json(STATE_FILE, self.state)
            except OSError:
                pass

    def resolve_ai(self, choice):
        models, default_id = self.nova.ai_models()
        if not models:
            raise RuntimeError("No AI is set up in the LDRAW-NOVA web app. "
                               "Open its Settings and add Claude or GPT.")
        if not choice or choice == AI_DEFAULT:
            return default_id or models[0]["id"], None
        for m in models:
            name = str(m.get("model_name") or "").strip()
            if choice in (name, name[:80], m.get("id")):
                return m["id"], m.get("model_name")
        raise RuntimeError(f"The AI {choice!r} isn't set up in the web app any more. "
                           "Press Refresh in the add-on's panel and pick again.")

    def run(self, job):
        job_id = job["id"]
        fields = job.get("fields") or {}
        note = lambda text: self.addon.job_note(job_id, text)
        prompt = (fields.get("prompt") or "").strip()
        if not prompt:
            # Prompt text can also come from the node wired into "in".
            wired = (job.get("inputs") or {}).get("in")
            if isinstance(wired, str) and wired.strip() and not os.path.exists(wired.strip()):
                prompt = wired.strip()
        gathered = self.gather_refs(job, note)
        prompt, summary = references.compose(prompt, gathered,
                                             fields.get("notes") or references.NOTES_USE)
        if not prompt:
            raise self.final("Nothing to build: the node has no prompt and no notes "
                             "were wired into it.")
        note("Sending " + (summary or "the prompt only"))
        for why in gathered.skipped:
            note("Left out " + why)
        ai_id, ai_name = self.resolve_ai(fields.get("ai"))
        plan_only = fields.get("mode") == MODE_PLAN

        node_key = f"{job.get('board', '')}/{job.get('node', '')}"
        chat_id = None
        if fields.get("chat") == CHAT_CONTINUE:
            chat_id = self.state["chats"].get(node_key)
            if chat_id and not self.nova.chat_exists(chat_id):
                note("That node's last chat is gone; starting a new one.")
                chat_id = None
        if not chat_id:
            chat_id = self.nova.create_chat(ai_id)["id"]
        self.state["chats"][node_key] = chat_id
        self.save_state()

        before = set((self.nova.get_chat(chat_id).get("models") or {}).keys())
        note(f"Sending to LDRAW-NOVA ({ai_name or 'default AI'}), chat {chat_id}")
        photos = gathered.photos
        batches = [photos[i:i + references.PHOTOS_PER_MESSAGE]
                   for i in range(0, len(photos), references.PHOTOS_PER_MESSAGE)] or [[]]
        start = 1
        for batch in batches[:-1]:
            # The web app takes 4 pictures a message: earlier ones go first,
            # as reference-only messages the AI just looks at.
            end = start + len(batch) - 1
            text = (f"Reference photos {start}-{end} of {len(photos)} for the LEGO build "
                    "I'll ask for next. Note briefly what matters for building it; "
                    "don't build anything yet.")
            note(f"Sending reference photos {start}-{end}")
            # The web app only knows "plan" and "agent"; plan + read-only
            # lets the AI look at the photos without building anything.
            self.nova.send(chat_id, text, ai_id, REFERENCE_OPTIONS,
                           [references.data_url(p) for p in batch])
            error = self.follow(job_id, chat_id, note)
            if error:
                raise RuntimeError("Sending reference photos failed: " + error)
            start = end + 1
        options = {"mode": "plan" if plan_only else "agent", "permissions": "full"}
        self.nova.send(chat_id, prompt, ai_id, options,
                       [references.data_url(p) for p in batches[-1]])

        error = self.follow(job_id, chat_id, note)

        chat = self.nova.get_chat(chat_id)
        new = [m for key, m in (chat.get("models") or {}).items() if key not in before]
        if plan_only:
            text = self.last_reply(chat)
            note("Plan: " + (text[:900] if text else "(no text)"))
            if error:
                raise RuntimeError(error)
            return {"message": "Plan written in the LDRAW-NOVA chat."}
        if not new:
            raise RuntimeError(error or "The AI finished without making a model. "
                               "Open the chat in LDRAW-NOVA to see what it said.")
        model = max(new, key=lambda m: m.get("created_at") or 0)
        if not model.get("model_url"):
            raise RuntimeError("The model the AI made can't be found in the web app.")
        model_file = model.get("file") or url_name(model["model_url"]) or "model.mpd"
        path = target_path(fields.get("output"), self.settings.get("folder"), model_file,
                           gathered.title)
        self.nova.download(model["model_url"], path)
        saved = [os.path.basename(path)]
        show = fields.get("show") or SHOW_NODE
        if truthy(fields.get("extras", "yes")) or show != SHOW_OFF:
            model = self.wait_for_extras(chat_id, model, note)
        if truthy(fields.get("extras", "yes")):
            saved += self.save_extras(model, model_file, path, note)
        parts = model.get("parts")
        summary = f"Saved {path}" + (f" ({parts} parts)" if parts else "")
        self.show_result(job_id, show, model, path, parts, note)
        note(summary)
        try:
            self.addon.notify("LEGO build finished", os.path.basename(path))
        except Exception:
            pass
        return {"output": path, "message": summary}

    def gather_refs(self, job, note):
        """Photos, notes and colours wired into the refs input."""
        wired = (job.get("inputs") or {}).get("refs")
        board_id, node_id = job.get("board"), job.get("node")
        if board_id and node_id and hasattr(self.addon, "get"):
            try:
                board = self.addon.get("/board?" + urllib.parse.urlencode({"id": board_id}))
                if references.wired_sources(board, node_id):
                    return references.gather(
                        board, node_id, references.board_image_fetcher(self.addon, board_id))
            except Exception as e:
                note(f"Couldn't read the board ({e}); using the wired photos only.")
        return references.from_paths(wired)

    def follow(self, job_id, chat_id, note):
        """Follow the turn until it ends. Returns the AI's error, or ""."""
        console = ConsoleFeed(self.addon, job_id)
        try:
            return self._follow(job_id, chat_id, note, console)
        finally:
            console.flush(force=True)

    def _follow(self, job_id, chat_id, note, console):
        error, last_note, drops = "", 0.0, 0
        while True:
            try:
                for event, data in self.nova.stream(chat_id):
                    if self.addon.cancelled(job_id):
                        self.nova.cancel(chat_id)
                        raise self.stopped()
                    console.event(event, data)
                    if event == "turn_error":
                        error = data.get("message") or "The AI reported an error."
                        note("Error: " + error)
                    elif event == "approval":
                        # Asked with full permissions this shouldn't happen;
                        # if it does, the node was told to run unattended.
                        self.nova.approve(chat_id, data.get("id"), True)
                    elif event == "snapshot" and not data.get("running"):
                        return error
                    elif event in ("done", "reconnect"):
                        if event == "done":
                            return error
                        break
                    line = describe(event, data)
                    if line and time.time() - last_note >= NOTE_EVERY:
                        note(line)
                        last_note = time.time()
            except NovaError as e:
                drops += 1
                if drops > STREAM_RETRIES:
                    raise RuntimeError(f"Lost track of the build: {e}")
                time.sleep(2)
            if self.addon.cancelled(job_id):
                self.nova.cancel(chat_id)
                raise self.stopped()
            chat = self.nova.get_chat(chat_id).get("chat") or {}
            if not chat.get("running"):
                return error

    def wait_for_extras(self, chat_id, model, note, limit=120):
        """The web app makes the snapshot and parts list just after the
        model; give it a little while rather than skipping them."""
        pending = ("queued", "rendering")
        waited = 0
        while (model.get("status") in pending or model.get("bom_status") in pending) \
                and waited < limit:
            if waited == 0:
                note("Waiting for the snapshot and parts list")
            time.sleep(3)
            waited += 3
            model = (self.nova.get_chat(chat_id).get("models") or {}).get(model.get("id"), model)
        return model

    def save_extras(self, model, model_file, path, note):
        saved = []
        stem = os.path.splitext(model_file)[0]
        new_stem = os.path.splitext(path)[0]
        for key in ("image_url", "bom_url"):
            url = model.get(key)
            status = model.get("status" if key == "image_url" else "bom_status")
            if not url or status not in (None, "ready"):
                continue
            name = url_name(url)
            suffix = name[len(stem):] if name.startswith(stem) else "-" + name
            try:
                self.nova.download(url, new_stem + suffix)
                saved.append(os.path.basename(new_stem + suffix))
            except NovaError as e:
                note(f"Couldn't save {name}: {e}")
        return saved

    def show_result(self, job_id, show, model, path, parts, note):
        """Put the finished snapshot in the node and/or on the board.
        Best effort: a build that saved its .mpd never fails over this."""
        if show == SHOW_OFF or not hasattr(self.addon, "job_preview"):
            return
        url = model.get("image_url")
        if not url or model.get("status") not in (None, "ready"):
            note("No snapshot to show on the board.")
            return
        try:
            image = self.nova.fetch(url)
        except Exception as e:
            note(f"Couldn't fetch the snapshot to show: {e}")
            return
        name = os.path.basename(path)
        if show in (SHOW_NODE, SHOW_BOTH):
            try:
                self.addon.job_preview(job_id, image)
            except Exception as e:
                note(f"Couldn't show the picture in the node: {e}")
        if show in (SHOW_GROUP, SHOW_BOTH):
            caption = name + (f" ({parts} parts)" if parts else "")
            try:
                self.addon.job_reference(job_id, image, caption=caption,
                                         title=name, group=RESULTS_GROUP)
            except Exception as e:
                note(f"Couldn't put the picture on the board: {e}")

    @staticmethod
    def last_reply(chat):
        for m in reversed(chat.get("messages") or []):
            if m.get("role") == "assistant" and isinstance(m.get("content"), str):
                return m["content"].strip()
        return ""

    def final(self, message):
        from blauncher_addon import JobStopped
        return JobStopped(message)

    def stopped(self):
        return self.final("stopped")


# --- the panel and node kinds -------------------------------------------------

def manifest_kinds():
    with open(os.path.join(HERE, "addon.json"), encoding="utf-8") as handle:
        return json.load(handle)["node_kinds"]


def kinds_with_ai(names):
    """The node kinds with the AI dropdown listing these models."""
    kinds = manifest_kinds()
    # B-Launcher wants 1-50 distinct options of at most 80 characters.
    options = list(dict.fromkeys(
        [AI_DEFAULT] + [str(n).strip()[:80] for n in names if n and str(n).strip()]))[:50]
    for kind in kinds:
        for entry in kind.get("fields", []):
            if entry["key"] == "ai":
                entry["options"] = options
                entry["default"] = AI_DEFAULT
    return kinds


def panel(settings, status="", models=()):
    items = [
        {"type": "label", "text": "LDRAW-NOVA builds LEGO models from the LEGO build node."},
        {"type": "entry", "id": "web_url", "text": "Web app address", "value": settings["web_url"]},
        {"type": "entry", "id": "folder", "text": "Default folder for .mpd files",
         "value": settings["folder"]},
        {"type": "button", "id": "save", "text": "Save and test connection"},
        {"type": "button", "id": "refresh", "text": "Refresh AI list"},
        {"type": "list", "id": "models", "text": "AI models in the web app",
         "items": list(models) or ["(press Refresh)"]},
    ]
    if status:
        items.append({"type": "label", "text": status})
    return {"title": "LDRAW-NOVA", "items": items}


def main():
    from blauncher_addon import Addon

    addon = Addon()
    # Version 0.1 kept these in the add-on's own folder; carry them over once.
    old_settings = load_json(os.path.join(ADDON_DIR, "settings.json"), {})
    old_state = load_json(os.path.join(ADDON_DIR, "state.json"), {"chats": {}})
    saved = load_json(SETTINGS_FILE, {}) if os.path.exists(SETTINGS_FILE) else old_settings
    state = load_json(STATE_FILE, {"chats": {}}) if os.path.exists(STATE_FILE) else old_state
    settings = {**DEFAULT_SETTINGS, **saved}
    builder = Builder(addon, settings, state)

    def refresh(status_ok="Connected."):
        builder.nova = Nova(settings["web_url"])
        try:
            models, default_id = builder.nova.ai_models()
        except NovaError as e:
            addon.set_panel(panel(settings, str(e)))
            return
        names = [m.get("model_name") or m["id"] for m in models]
        labels = [n + ("  (default)" if m["id"] == default_id else "")
                  for n, m in zip(names, models)]
        try:
            addon.add_node_kinds(kinds_with_ai(names))
        except Exception as e:
            addon.log(f"Couldn't update the AI list on the node: {e}")
        addon.set_panel(panel(settings, f"{status_ok} {len(models)} AI model(s) found.", labels))

    @addon.on("button")
    def pressed(event):
        values = event.get("values") or {}
        if event.get("id") == "save":
            settings["web_url"] = (values.get("web_url") or DEFAULT_SETTINGS["web_url"]).strip()
            settings["folder"] = (values.get("folder") or DEFAULT_SETTINGS["folder"]).strip()
            try:
                save_json(SETTINGS_FILE, settings)
            except OSError as e:
                addon.log(f"Couldn't save settings: {e}")
            refresh("Saved and connected.")
        elif event.get("id") == "refresh":
            refresh()

    @addon.on("job")
    def job(event):
        if event.get("node_kind") != "lego_build":
            raise RuntimeError(f"Unknown node {event.get('node_kind')!r}")
        return builder.run(event)

    addon.set_panel(panel(settings))
    threading.Thread(target=refresh, daemon=True).start()
    addon.log("LDRAW-NOVA add-on started")
    addon.run()


if __name__ == "__main__":
    main()
