"""A small client for writing B-Launcher add-ons. Standard library only.

Copy this file into your add-on's folder and import it. It is nothing more
than the HTTP calls in ADDONS.md wrapped up, so anything here can equally be
done with urllib, requests or any other language that speaks HTTP.

    from blauncher_addon import Addon

    addon = Addon()                     # reads the URL and token B-Launcher set
    addon.set_panel({"title": "Mine", "items": [
        {"type": "button", "id": "go", "text": "Go"}]})

    @addon.on("button")
    def pressed(event):
        addon.notify("Pressed", f"{event['id']} with {event['values']}")

    @addon.on("job")
    def run(job):
        ...                             # do the work
        if addon.cancelled(job["id"]):  # Pause board, Remove, a restart
            raise JobStopped("stopped")
        return "done"                   # or raise to fail the node
        # or: return {"output": "C:/out/castle-v2.mpd", "message": "built"}
        # to hand the next node the file this one really made

    addon.run()                         # until B-Launcher says stop

Each job runs on a thread of its own, so a "cancel" for it can arrive
while it works: addon.cancelled(job_id) turns true, and an on("cancel")
handler, if there is one, is called with the event.
"""
import json
import os
import sys
import threading
import time
import traceback
import urllib.error
import urllib.request


class AddonError(Exception):
    """B-Launcher said no. .status is the HTTP code."""

    def __init__(self, status, message):
        super().__init__(f"{status}: {message}")
        self.status = status


class JobStopped(Exception):
    """Raise from a job handler to fail it without B-Launcher retrying it."""


class Addon:
    def __init__(self, url=None, token=None):
        self.url = (url or os.environ.get("BLAUNCHER_API_URL", "")).rstrip("/")
        self.token = token or os.environ.get("BLAUNCHER_ADDON_TOKEN", "")
        self.id = os.environ.get("BLAUNCHER_ADDON_ID", "")
        if not self.url or not self.token:
            raise SystemExit("This is a B-Launcher add-on: B-Launcher starts "
                             "it, with the address and token it needs.")
        self.handlers = {}
        self.running = False
        self._cancelled = {}            # job id -> why

    def cancelled(self, job_id):
        """Why B-Launcher asked this job to stop, or "" if it hasn't."""
        return self._cancelled.get(job_id, "")

    # --- Raw calls ---
    def request(self, method, path, body=None, timeout=60):
        data = json.dumps(body).encode("utf-8") if body is not None else None
        request = urllib.request.Request(self.url + path, data=data,
                                         method=method)
        request.add_header("Authorization", f"Bearer {self.token}")
        if data is not None:
            request.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(request, timeout=timeout) as answer:
                return json.loads(answer.read().decode("utf-8") or "{}")
        except urllib.error.HTTPError as e:
            try:
                message = json.loads(e.read().decode("utf-8")).get("error")
            except Exception:
                message = e.reason
            raise AddonError(e.code, message) from None

    def get(self, path, **kw):
        return self.request("GET", path, **kw)

    def post(self, path, body=None, **kw):
        return self.request("POST", path, body or {}, **kw)

    # --- The add-on's own channel ---
    def me(self):
        return self.get("/addon/me")

    def events(self, wait=20):
        return self.get(f"/addon/events?wait={wait}",
                        timeout=wait + 15).get("events", [])

    def log(self, message):
        self.post("/addon/log", {"message": str(message)})

    def notify(self, title, message=""):
        self.post("/addon/notify", {"title": title, "message": message})

    def set_panel(self, panel):
        return self.post("/addon/panel", panel)

    def add_node_kinds(self, kinds):
        return self.post("/addon/node_kinds", {"node_kinds": kinds})

    def job_note(self, job_id, message):
        self.post(f"/addon/jobs/{job_id}/note", {"message": str(message)})

    @staticmethod
    def _image_b64(image):
        """A picture as base64: given as bytes, or as a file's path."""
        import base64
        if isinstance(image, (bytes, bytearray)):
            data = bytes(image)
        else:
            with open(image, "rb") as handle:
                data = handle.read()
        return base64.b64encode(data).decode("ascii")

    def job_preview(self, job_id, image):
        """Show a picture of what the job made inside its node."""
        return self.post(f"/addon/jobs/{job_id}/preview",
                         {"image": self._image_b64(image)})

    def job_reference(self, job_id, image, caption="", title="", group=""):
        """Put a picture on the job's board as a Reference, in the group
        called `group` (made beside the node if there isn't one)."""
        return self.post(f"/addon/jobs/{job_id}/reference",
                         {"image": self._image_b64(image), "caption": caption,
                          "title": title, "group": group})

    def job_done(self, job_id, ok=True, message="", error="", output=None,
                 retry=None):
        """Answer a job. `output` is the file it made, for the next node;
        retry=False says a failure is final and not worth another go."""
        body = {"ok": bool(ok), "message": str(message), "error": str(error)}
        if output:
            body["output"] = str(output)
        if retry is not None:
            body["retry"] = bool(retry)
        self.post(f"/addon/jobs/{job_id}/done", body)

    # --- The loop ---
    def on(self, event_type):
        """Decorator: call this function for every event of that type."""
        def register(fn):
            self.handlers[event_type] = fn
            return fn
        return register

    def run(self):
        """Wait for events and hand them out, until told to stop.

        A job handler's return value becomes the node's note; raising fails
        the node with the exception's text. Either way B-Launcher is told,
        so a bug in a handler is a failed node rather than a board waiting
        forever.
        """
        self.running = True
        failures = 0
        while self.running:
            try:
                batch = self.events()
                failures = 0
            except AddonError as e:
                if e.status in (401, 402, 403):
                    print(f"B-Launcher refused this add-on: {e}", file=sys.stderr)
                    return
                raise
            except (urllib.error.URLError, OSError):
                # B-Launcher has gone. Give it a moment in case it's busy,
                # then stop rather than spin.
                failures += 1
                if failures > 5:
                    return
                time.sleep(1)
                continue
            for event in batch:
                kind = event.get("type")
                if kind == "stop":
                    self.running = False
                    break
                handler = self.handlers.get(kind)
                if kind == "job":
                    threading.Thread(target=self._run_job,
                                     args=(handler, event),
                                     daemon=True).start()
                    continue
                if kind == "cancel":
                    self._cancelled[event.get("id")] = (event.get("reason")
                                                        or "cancelled")
                if handler is not None:
                    try:
                        handler(event)
                    except Exception:
                        traceback.print_exc()

    def _run_job(self, handler, job):
        try:
            self._answer_job(handler, job)
        finally:
            self._cancelled.pop(job["id"], None)

    def _answer_job(self, handler, job):
        if handler is None:
            self.job_done(job["id"], ok=False,
                          error="This add-on doesn't run board jobs.")
            return
        try:
            result = handler(job)
        except JobStopped as e:
            self.job_done(job["id"], ok=False, error=str(e) or "stopped",
                          retry=False)
            return
        except Exception as e:
            traceback.print_exc()
            self.job_done(job["id"], ok=False, error=str(e) or type(e).__name__)
            return
        if isinstance(result, dict):
            self.job_done(job["id"], ok=True,
                          message=result.get("message") or "",
                          output=result.get("output"))
        else:
            self.job_done(job["id"], ok=True, message=result or "")
