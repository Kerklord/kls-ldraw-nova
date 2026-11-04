# LDRAW-NOVA add-on for B-Launcher

Build LEGO models from a B-Launcher board. The add-on puts a **LEGO build**
node on the board. When the board reaches it, the node sends the prompt to the
LDRAW-NOVA web app, follows the build in the queue log, and saves the
finished `.mpd` where you chose. Then it hands that file to the next node, so
**LEGO build → Open in program (Stud.io)** opens the model when it's done.

Needs B-Launcher 1.6 with add-on guide 1.2 or newer (dropdown fields, job
outputs, cancel events), and the LDRAW-NOVA web app running. The web app is
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
| Prompt | What to build. Board values work, e.g. `{prompt}`, which you can set from B-Launcher Remote. |
| AI | `Default` (the web app's default) or one of its models. Press **Refresh AI list** after changing models in the web app. |
| Mode | `Build`, or `Plan only` to have the AI write a plan without building. The plan appears in the queue log and the chat. |
| Chat | `New chat` each run, or `Continue this node's chat` to send a follow-up to the chat this node used last time. |
| Save .mpd as | A file (`D:\LEGO\castle.mpd`) or a folder. Blank uses the panel's default folder. An existing file is never overwritten: it saves `castle-v2.mpd` instead. |
| Also save snapshot and parts list | Saves the PNG preview and the parts list next to the `.mpd`. |

Pausing, removing or restarting the board stops the AI in the web app too.
A failed build isn't retried automatically (`"retries": 0`). Fallback
prompts come in a later version.

## Tests

```
python -m pytest integrations/b-launcher/tests -q
```

The tests run the node against a fake web app, so Docker isn't needed.
