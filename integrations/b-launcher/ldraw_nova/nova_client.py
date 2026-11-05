"""A small client for the LDRAW-NOVA web app's HTTP API. Standard library only.

Only the routes the add-on needs: the AI models set up in Settings, chats,
sending a message, following a turn's progress, cancelling it, and
downloading the files a chat produced.
"""
import json
import os
import socket
import urllib.error
import urllib.parse
import urllib.request


class NovaError(Exception):
    """The web app said no, or could not be reached. `status` is the HTTP
    status when it answered, else None."""

    def __init__(self, message, status=None):
        super().__init__(message)
        self.status = status


class Nova:
    def __init__(self, base_url):
        self.base = (base_url or "http://localhost:8765").strip().rstrip("/")

    # --- raw calls --------------------------------------------------------

    def _open(self, method, path, body=None, timeout=60):
        data = json.dumps(body).encode("utf-8") if body is not None else None
        request = urllib.request.Request(self.base + path, data=data, method=method)
        if data is not None:
            request.add_header("Content-Type", "application/json")
        try:
            return urllib.request.urlopen(request, timeout=timeout)
        except urllib.error.HTTPError as e:
            try:
                detail = json.loads(e.read().decode("utf-8")).get("detail")
            except Exception:
                detail = e.reason
            raise NovaError(f"LDRAW-NOVA answered {e.code}: {detail}", e.code) from None
        except (urllib.error.URLError, socket.timeout, OSError) as e:
            raise NovaError(f"Can't reach LDRAW-NOVA at {self.base} ({e}). "
                            "Is the web app running?") from None

    def _json(self, method, path, body=None, timeout=60):
        with self._open(method, path, body, timeout) as answer:
            return json.loads(answer.read().decode("utf-8") or "{}")

    # --- AI models ----------------------------------------------------------

    def ai_models(self):
        """(models, default_id). Each model has at least id and model_name."""
        data = self._json("GET", "/api/llm-models")
        return data.get("models") or [], data.get("default_id")

    # --- chats ----------------------------------------------------------------

    def create_chat(self, llm_model_id=None):
        return self._json("POST", "/api/chats", {"llm_model_id": llm_model_id})

    def get_chat(self, chat_id):
        return self._json("GET", f"/api/chats/{urllib.parse.quote(chat_id)}")

    def chat_exists(self, chat_id):
        """True or False when the web app says; raises when it can't be asked
        (down, slow, an error), so a hiccup never passes for a deleted chat."""
        try:
            self.get_chat(chat_id)
            return True
        except NovaError as e:
            if e.status == 404:
                return False
            raise

    def send(self, chat_id, text, llm_model_id=None, options=None, images=()):
        body = {"text": text, "llm_model_id": llm_model_id,
                "options": options or {}, "images": list(images)}
        return self._json("POST", f"/api/chats/{urllib.parse.quote(chat_id)}/messages", body)

    def cancel(self, chat_id):
        try:
            self._json("POST", f"/api/chats/{urllib.parse.quote(chat_id)}/cancel", {}, timeout=15)
        except NovaError:
            pass

    def approve(self, chat_id, approval_id, approved=True):
        self._json("POST", f"/api/chats/{urllib.parse.quote(chat_id)}/approvals/{approval_id}",
                   {"approved": bool(approved)})

    def stream(self, chat_id, timeout=30):
        """Yield (event, data) from a turn's progress stream until it ends.

        The web app sends an `activity` event at least every 5 seconds, so a
        caller looping over this gets a chance to check for a cancel often.
        """
        answer = self._open("GET", f"/api/chats/{urllib.parse.quote(chat_id)}/stream",
                            timeout=timeout)
        event, data = "message", []
        with answer:
            lines = iter(answer)
            while True:
                try:
                    raw = next(lines)
                except StopIteration:
                    return
                except (socket.timeout, OSError, ValueError) as e:
                    # A stalled or dropped stream: the caller reconnects.
                    raise NovaError(f"The progress stream dropped ({e}).") from None
                line = raw.decode("utf-8", "replace").rstrip("\r\n")
                if not line:
                    if data:
                        try:
                            payload = json.loads("\n".join(data))
                        except ValueError:
                            payload = {}
                        yield event, payload
                    event, data = "message", []
                    continue
                if line.startswith("event:"):
                    event = line[6:].strip()
                elif line.startswith("data:"):
                    data.append(line[5:].lstrip())

    # --- files ------------------------------------------------------------

    def fetch(self, url, limit=20 * 1024 * 1024):
        """The bytes of a file the web app serves (a /files/... url)."""
        with self._open("GET", url.split("?", 1)[0], timeout=120) as answer:
            data = answer.read(limit + 1)
        if len(data) > limit:
            raise NovaError("That file is too big.")
        return data

    def download(self, url, target):
        """Save a file the web app serves (a /files/... url) to `target`."""
        path = url.split("?", 1)[0]
        os.makedirs(os.path.dirname(os.path.abspath(target)), exist_ok=True)
        partial = target + ".part"
        with self._open("GET", path, timeout=120) as answer, open(partial, "wb") as out:
            while True:
                chunk = answer.read(65536)
                if not chunk:
                    break
                out.write(chunk)
        os.replace(partial, target)
        return target
