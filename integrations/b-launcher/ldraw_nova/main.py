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

from nova_client import Nova, NovaError

HERE = os.path.dirname(os.path.abspath(__file__))
ADDON_DIR = os.environ.get("BLAUNCHER_ADDON_DIR") or HERE
SETTINGS_FILE = os.path.join(ADDON_DIR, "settings.json")
STATE_FILE = os.path.join(ADDON_DIR, "state.json")

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


# --- small helpers ------------------------------------------------------------

def load_json(path, default):
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
        return data if isinstance(data, type(default)) else default
    except (OSError, ValueError):
        return default


def save_json(path, data):
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


def target_path(output_field, folder, model_file):
    """Where to save the model: the node's Save as, else the default folder."""
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
        return data.get("message") or data.get("phase")
    if event == "activity":
        return data.get("phase")
    return None


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
            if choice in (m.get("model_name"), m.get("id")):
                return m["id"], m.get("model_name")
        raise RuntimeError(f"The AI {choice!r} isn't set up in the web app any more. "
                           "Press Refresh in the add-on's panel and pick again.")

    def run(self, job):
        job_id = job["id"]
        fields = job.get("fields") or {}
        prompt = (fields.get("prompt") or "").strip()
        if not prompt:
            first = next(iter((job.get("inputs") or {}).values()), "")
            prompt = str(first or "").strip() if not os.path.exists(str(first or "")) else ""
        if not prompt:
            raise self.final("The node has no prompt. Type one in its Prompt field.")

        note = lambda text: self.addon.job_note(job_id, text)
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
        options = {"mode": "plan" if plan_only else "agent", "permissions": "full"}
        self.nova.send(chat_id, prompt, ai_id, options)

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
        path = target_path(fields.get("output"), self.settings.get("folder"), model_file)
        self.nova.download(model["model_url"], path)
        saved = [os.path.basename(path)]
        if truthy(fields.get("extras", "yes")):
            model = self.wait_for_extras(chat_id, model, note)
            saved += self.save_extras(model, model_file, path, note)
        parts = model.get("parts")
        summary = f"Saved {path}" + (f" ({parts} parts)" if parts else "")
        note(summary)
        try:
            self.addon.notify("LEGO build finished", os.path.basename(path))
        except Exception:
            pass
        return {"output": path, "message": summary}

    def follow(self, job_id, chat_id, note):
        """Follow the turn until it ends. Returns the AI's error, or ""."""
        error, last_note, drops = "", 0.0, 0
        while True:
            try:
                for event, data in self.nova.stream(chat_id):
                    if self.addon.cancelled(job_id):
                        self.nova.cancel(chat_id)
                        raise self.stopped()
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
    options = [AI_DEFAULT] + [n for n in dict.fromkeys(names) if n and n != AI_DEFAULT][:49]
    for kind in kinds:
        for entry in kind.get("fields", []):
            if entry["key"] == "ai":
                entry["options"] = [o[:80] for o in options]
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
    settings = {**DEFAULT_SETTINGS, **load_json(SETTINGS_FILE, {})}
    builder = Builder(addon, settings, load_json(STATE_FILE, {"chats": {}}))

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
