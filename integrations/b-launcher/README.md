# LDRAW-NOVA add-on for B-Launcher

Build LEGO models from a B-Launcher board. The add-on puts a **LEGO build**
node on the board. When the board reaches it, the node sends the prompt to the
LDRAW-NOVA web app, follows the build in the queue log, and saves the
finished `.mpd` where you chose. Then it hands that file to the next node, so
**LEGO build → Open in program (Stud.io)** opens the model when it's done.

Needs B-Launcher 1.6 with add-on guide 1.5 or newer (dropdown fields, job
outputs, cancel events, the Group `photos` output, job pictures on the board, and the node Console), and the LDRAW-NOVA web app running. The web app is
at http://localhost:8765 by default. The add-on uses only Python's standard
library.

## Install

1. Zip the `ldraw_nova` folder, keeping the folder itself inside the zip.
2. In B-Launcher, go to **Settings → Add-ons → Install from zip…**, then
   switch the add-on on.
3. Open its panel from the Tools tab. Check the web app address and the
   default folder, then press **Save and test connection**. The AI dropdown
   on the node fills with the models set up in the web app's Settings.

## The LEGO build node

| Field | What it does |
| --- | --- |
| Notes from refs | *Use notes when Prompt is empty* (default), *Add notes to my prompt*, or *Ignore notes*. |
| Prompt | What to build. Leave it empty to use the notes from the wired group. Board values work, e.g. `{prompt}`, which you can set from B-Launcher Remote. |
| AI | `Default` (the web app's default) or one of its models. Press **Refresh AI list** after changing models in the web app. |
| Mode | `Build`, or `Plan only` to have the AI write a plan without building. The plan appears in the queue log and the chat. |
| Chat | `New chat` each run, or `Continue this node's chat` to send a follow-up to the chat this node used last time. |
| Save .mpd as | A file (`D:\LEGO\castle.mpd`) or a folder. Blank uses the panel's default folder. An existing file is never overwritten: it saves `castle-v2.mpd` instead. |
| Also save snapshot and parts list | Saves the PNG preview and the parts list next to the `.mpd`. |
| Show the finished picture | `In the node` (default) shows the model's snapshot inside the node; double-click the node to see it full size, Escape closes it. `On the board, in a group` adds the snapshot as a Reference, captioned with the file name and part count, in a group called **LEGO builds** beside the node (made the first time, reused after). `Both`, or `Don't show it`. Works even when the snapshot isn't saved to disk. |

## Watching it work

Click the LEGO build node while it runs: the **Console** at the bottom of the panel on the right shows the LDRAW-NOVA chat live. You see the AI's reply as it writes it, each tool it runs (`> build: …`), the tool output, and any error. When the run ends, the console keeps the last run's log until the node runs again. The queue log still gets the short progress notes.

## References: wire a Group into refs

Wire a Group box's **photos** output (or single Reference nodes) into the
node's **refs** input. The add-on reads the board and uses only:

- **Photos**, top row first and left to right, including groups inside the
  group. Captions go into the prompt as "Photo 2: side view". The web app
  takes 4 pictures per message, so with more than 4 the earlier ones are sent
  first as reference-only messages, then the build prompt with the last
  ones. Up to 16 photos are sent. PNG, JPEG and WebP only, 5 MB each.
- **Notes**, merged into one prompt in board order. A note's title becomes
  its heading. Notes titled **Avoid**, **Parts** or **Colours** become rules
  ("Avoid:", "Parts to use:", "Colour rules:") instead of description, and
  are added even when the node has its own prompt.
- **Colours nodes.** Each colour is matched to the nearest solid LDraw
  colour (LDConfig.ldr, the same names Stud.io uses), e.g. "Dark Red (320)".

Everything else in the group (sketches, render nodes...) is ignored. The
group's title names the saved file (`Castle gate` → `castle-gate.mpd`) when
**Save .mpd as** is empty or a folder. The queue log lists what was sent and
anything left out.

## Settings

The panel's settings and each node's chat are kept in
`%APPDATA%\LDRAW-NOVA addon`, outside the add-on's folder, so reinstalling
the add-on keeps them.

Pausing, removing or restarting the board stops the AI in the web app too.
A failed build isn't retried automatically (`"retries": 0`).

## Tests

```
python -m pytest integrations/b-launcher/tests -q
```

The tests run the node against a fake web app, so Docker isn't needed.
