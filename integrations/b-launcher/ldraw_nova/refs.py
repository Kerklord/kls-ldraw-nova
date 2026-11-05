"""Reference material for a LEGO build: what is wired into the node's refs
input, read from the board, and turned into a prompt and a list of photos.

Only photos, notes and Colours nodes are used; anything else in a group
(sketches, render nodes...) is left out.
"""
import base64
import json
import math
import os
import re
import urllib.error
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))

NOTES_USE = "Use notes when Prompt is empty"
NOTES_ADD = "Add notes to my prompt"
NOTES_IGNORE = "Ignore notes"
NOTES_OPTIONS = [NOTES_USE, NOTES_ADD, NOTES_IGNORE]

# Note titles that become rules rather than part of the description.
SPECIAL = {
    "avoid": "Avoid",
    "parts": "Parts to use",
    "colours": "Colour rules",
    "colors": "Colour rules",
}

MAX_IMAGE_BYTES = 5 * 1024 * 1024      # the web app's limit per image
MAX_PHOTOS = 16                        # 4 messages of 4
PHOTOS_PER_MESSAGE = 4


class Photo:
    def __init__(self, data, caption="", name=""):
        self.data, self.caption, self.name = data, caption, name


class Refs:
    """Everything gathered for one job."""

    def __init__(self):
        self.title = ""            # the first wired group's title
        self.notes = []            # (title, text) in board order
        self.photos = []           # Photo, in board order
        self.colours = []          # "#rrggbb" from Colours nodes
        self.skipped = []          # why something wasn't used


# --- reading the board ------------------------------------------------------

def board_order(node):
    return (float(node.get("y") or 0), float(node.get("x") or 0))


def wired_sources(board, node_id, port="refs"):
    """Ids of the nodes wired into this node's refs input, in wiring order."""
    return [link.get("from") for link in board.get("links") or []
            if link.get("to") == node_id and link.get("to_port") == port]


def group_contents(board, group_id, seen=None):
    """Every node inside a group, groups inside it included, in board order."""
    seen = seen if seen is not None else set()
    if group_id in seen:
        return []
    seen.add(group_id)
    nodes = {n["id"]: n for n in board.get("nodes") or []}
    group = next((g for g in board.get("groups") or [] if g.get("id") == group_id), None)
    member_ids = list((group or {}).get("members") or [])
    # Belt and braces: also anything that says it sits in this group, and
    # any group whose parent is this one.
    member_ids += [n["id"] for n in nodes.values() if n.get("group") == group_id]
    member_ids += [g["id"] for g in board.get("groups") or [] if g.get("parent") == group_id]
    out = []
    for member in dict.fromkeys(member_ids):
        node = nodes.get(member)
        if node is None:
            continue
        if node.get("kind") == "frame":
            out.extend(group_contents(board, member, seen))
        else:
            out.append(node)
    return sorted(out, key=board_order)


def group_title(board, group_id):
    group = next((g for g in board.get("groups") or [] if g.get("id") == group_id), None)
    node = next((n for n in board.get("nodes") or [] if n.get("id") == group_id), None)
    title = (group or {}).get("title") or ((node or {}).get("fields") or {}).get("title") \
        or (node or {}).get("title") or ""
    return "" if title.strip().lower() == "group" else title.strip()


def gather(board, node_id, fetch_image):
    """Read the refs wired into a node from the board.

    `fetch_image(node_id)` returns a picture's bytes. Returns a Refs.
    """
    refs = Refs()
    nodes = {n["id"]: n for n in board.get("nodes") or []}
    items, seen = [], set()
    for source in wired_sources(board, node_id):
        node = nodes.get(source)
        if node is None:
            continue
        if node.get("kind") == "frame":
            if not refs.title:
                refs.title = group_title(board, source)
            members = group_contents(board, source)
        else:
            members = [node]
        for member in members:
            if member["id"] not in seen:
                seen.add(member["id"])
                items.append(member)

    for node in items:
        kind = node.get("kind")
        fields = node.get("fields") or {}
        if kind == "note":
            text = (fields.get("text") or "").strip()
            if text:
                refs.notes.append(((node.get("title") or "").strip(), text))
        elif kind == "image" and node.get("image"):
            caption = (node.get("caption") or fields.get("caption") or "").strip()
            name = caption or node.get("title") or node["id"]
            try:
                data = fetch_image(node["id"])
            except Exception as e:      # one bad picture shouldn't stop the build
                refs.skipped.append(f"photo {name!r}: couldn't read it ({e})")
                continue
            add_photo(refs, data, caption, name)
        elif kind == "palette":
            for colour in node.get("colours") or []:
                if colour not in refs.colours:
                    refs.colours.append(colour)
    return refs


def from_paths(paths):
    """Refs from a plain list of picture paths (when the board can't be read)."""
    refs = Refs()
    if isinstance(paths, str):
        paths = [paths] if paths else []
    for path in paths or []:
        name = os.path.basename(str(path))
        try:
            with open(path, "rb") as handle:
                data = handle.read(MAX_IMAGE_BYTES + 1)
        except OSError as e:
            refs.skipped.append(f"photo {name!r}: couldn't read it ({e})")
            continue
        add_photo(refs, data, "", name)
    return refs


def image_type(data):
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if data.startswith(b"\xff\xd8\xff"):
        return "jpeg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    return None


def add_photo(refs, data, caption, name):
    if image_type(data) is None:
        refs.skipped.append(f"photo {name!r}: not a PNG, JPEG or WebP")
    elif len(data) > MAX_IMAGE_BYTES:
        refs.skipped.append(f"photo {name!r}: over 5 MB")
    elif len(refs.photos) >= MAX_PHOTOS:
        refs.skipped.append(f"photo {name!r}: more than {MAX_PHOTOS} photos")
    else:
        refs.photos.append(Photo(data, caption, name))


def data_url(photo):
    return (f"data:image/{image_type(photo.data)};base64,"
            + base64.b64encode(photo.data).decode("ascii"))


# --- colours ----------------------------------------------------------------

def _load_colours():
    with open(os.path.join(HERE, "ldraw_colours.json"), encoding="utf-8") as handle:
        return json.load(handle)["colours"]


def _lab(hex_colour):
    """sRGB #rrggbb to CIE Lab, so "closest" means closest to the eye."""
    value = hex_colour.lstrip("#")
    rgb = [int(value[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    lin = [c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in rgb]
    x = (0.4124 * lin[0] + 0.3576 * lin[1] + 0.1805 * lin[2]) / 0.95047
    y = (0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2])
    z = (0.0193 * lin[0] + 0.1192 * lin[1] + 0.9505 * lin[2]) / 1.08883
    f = [t ** (1 / 3) if t > 0.008856 else 7.787 * t + 16 / 116 for t in (x, y, z)]
    return (116 * f[1] - 16, 500 * (f[0] - f[1]), 200 * (f[1] - f[2]))


_COLOURS = None


def nearest_ldraw(hex_colour):
    """(code, name) of the LDraw colour closest to #rrggbb, or None."""
    global _COLOURS
    if not re.fullmatch(r"#?[0-9A-Fa-f]{6}", str(hex_colour or "")):
        return None
    if _COLOURS is None:
        _COLOURS = [(code, name, _lab(value)) for code, name, value in _load_colours()]
    target = _lab(hex_colour)
    code, name, _ = min(_COLOURS, key=lambda c: math.dist(c[2], target))
    return code, name


# --- the prompt -------------------------------------------------------------

def compose(node_prompt, refs, notes_mode=NOTES_USE):
    """(prompt, summary): the text to send and a line saying what went in."""
    node_prompt = (node_prompt or "").strip()
    used = []
    if notes_mode == NOTES_IGNORE:
        regular, special = [], []
    else:
        regular = [(t, x) for t, x in refs.notes if t.lower() not in SPECIAL]
        special = [(t, x) for t, x in refs.notes if t.lower() in SPECIAL]

    def merged(notes):
        parts = []
        for title, text in notes:
            heading = title if title and title.lower() not in ("note", "notes") else ""
            parts.append(f"{heading}:\n{text}" if heading else text)
        return "\n\n".join(parts)

    sections = []
    if node_prompt:
        sections.append(node_prompt)
        if notes_mode == NOTES_ADD and regular:
            sections.append("More detail from the board:\n\n" + merged(regular))
            used += [t or "untitled" for t, _ in regular]
    elif notes_mode != NOTES_IGNORE and regular:
        sections.append(merged(regular))
        used += [t or "untitled" for t, _ in regular]

    if not sections:
        return "", ""

    by_heading = {}
    for title, text in special:
        by_heading.setdefault(SPECIAL[title.lower()], []).append(text)
        used.append(title)
    for heading, texts in by_heading.items():
        sections.append(f"{heading}:\n" + "\n".join(texts))

    matched = []
    if notes_mode != NOTES_IGNORE:
        for colour in refs.colours:
            hit = nearest_ldraw(colour)
            if hit and hit not in matched:
                matched.append(hit)
        if matched:
            sections.append("Use these LDraw colours: "
                            + ", ".join(f"{name} ({code})" for code, name in matched) + ".")

    captions = [f"Photo {i}: {p.caption}" for i, p in enumerate(refs.photos, 1) if p.caption]
    if refs.photos:
        line = f"{len(refs.photos)} reference photo(s) are attached to this chat."
        sections.append(line + ("\n" + "\n".join(captions) if captions else ""))

    summary = []
    if used:
        summary.append(f"{len(used)} note(s): " + ", ".join(used))
    if refs.photos:
        summary.append(f"{len(refs.photos)} photo(s)")
    if matched:
        summary.append("colours: " + ", ".join(f"{name} ({code})" for code, name in matched))
    return "\n\n".join(sections), "; ".join(summary)


def slug(title):
    text = re.sub(r"[^a-z0-9]+", "-", (title or "").lower()).strip("-")
    return text[:60].strip("-")


# --- talking to B-Launcher for pictures ----------------------------------------

def board_image_fetcher(addon, board_id):
    """A function reading one board picture's bytes over B-Launcher's API."""
    def fetch(node_id):
        query = urllib.parse.urlencode({"board": board_id, "node": node_id})
        request = urllib.request.Request(f"{addon.url}/board_image?{query}")
        request.add_header("Authorization", f"Bearer {addon.token}")
        with urllib.request.urlopen(request, timeout=60) as answer:
            return answer.read(MAX_IMAGE_BYTES + 1)
    return fetch
