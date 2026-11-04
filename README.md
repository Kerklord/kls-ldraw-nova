# ldraw-nova

Build LEGO models with an AI agent, ordinary JSON plans and LDraw parts. The tools find parts and reference constructions, generate editable MPDs, check geometry, render previews and compare parts lists. Python generators support larger procedural designs.

[Watch the demonstration](https://youtu.be/YDjjxGqWpgU) · [Project background](docs/background.md)

## Quick install

**Windows.** Download [`Install-LDRAW-NOVA.bat`](https://raw.githubusercontent.com/Kerklord/ldraw-nova/master/Install-LDRAW-NOVA.bat) (right-click, *Save link as*) and double-click it. If Windows warns about a downloaded file, choose *More info* then *Run anyway*. It asks how you want to use LDRAW-NOVA:

| Mode | What you get | What it installs |
|---|---|---|
| **Web app** (default) | A chat page in your browser with a 3D viewer and VR. Sign in to Claude or ChatGPT in the app's Settings and add an agent. | Docker Desktop (via winget), this repository and [ldraw-nova-docker](https://github.com/anteloc/ldraw-nova-docker) in `%USERPROFILE%\LDRAW-NOVA` (or a folder you pick), then builds the app. The first build takes 15 to 40 minutes and about 5 GB; the image brings its own LDraw library. |
| **Terminal** | Claude Code and/or OpenAI Codex (GPT) in a command window, driving `./ldraw-agent` directly. | WSL with Ubuntu, then everything below inside it, using your own LDraw folder (picked or detected, copied into Linux for speed). |
| **Both** | Both of the above. | |

Desktop shortcuts: **LDRAW-NOVA** starts the web app and opens it in your browser, **Stop LDRAW-NOVA** stops it, **LDRAW-NOVA models** opens its data folder; terminal mode adds **LDRAW-NOVA Claude (terminal)** and/or **LDRAW-NOVA GPT (terminal)** plus **LDRAW-NOVA files**. If WSL or Docker Desktop is new, the installer tells you when to restart; then double-click it again and it continues. From PowerShell, the same installer runs with:

```powershell
irm https://raw.githubusercontent.com/Kerklord/ldraw-nova/master/install.ps1 | iex
```

**macOS, Linux or WSL** (terminal mode; needs Homebrew on macOS). Checks every requirement and installs what is missing (git, uv, Python 3.12, Poppler, LeoCAD 23.03+, Claude Code and/or Codex), clones the repository, points it at your LDraw library, runs `./setup.sh` and a test build. Running it again is safe.

```sh
curl -fsSL https://raw.githubusercontent.com/Kerklord/ldraw-nova/master/install.sh | bash
```

**Updating.** Double-click **Update LDRAW-NOVA** (terminal mode: **Update LDRAW-NOVA (terminal)**). For the web app it downloads the latest code and builds the new version while the current one keeps running; if the build fails, nothing changes. Your chats, models and logins are kept, and only changed Docker layers rebuild, so an update usually takes minutes. The previous three versions are saved: **Revert LDRAW-NOVA update** lists them and switches back instantly, and running it again returns you to the version you left. In terminal mode, Revert steps back one update at a time and Update brings you forward again. On macOS or Linux, run `~/.local/share/ldraw-nova/install.sh --update` or `--revert` (add `--dir` if you installed elsewhere); terminal mode refuses to update over edited tracked files.

Options such as `--ai claude|gpt|both`, `--repo original`, `--dir`, `--ldraw PATH` (a folder or `complete.zip`) and `--with-lsynth` skip the questions; see `install.sh --help`. `install.ps1` takes `-Mode web|terminal|both`, `-AI`, `-WebDir`, `-LDraw`, `-Repo`, and `-Update` or `-Revert` with `-KeepVersions N`. From a cloned copy, run `./install.sh` or `powershell -ExecutionPolicy Bypass -File install.ps1`.

## Start locally

You need Python 3.12+, an installed LDraw parts library, Poppler (`pdftotext`) and LeoCAD on `PATH`. The repository includes the reference data and connector metadata used by the tools.

```sh
export LDRAW_DIR=/path/to/ldraw
./setup.sh
./ldraw-agent review examples/plan-basics/bridge.plan.json --outdir output/bridge-review
```

Open the PNGs in `output/bridge-review/previews/`. The same folder contains the model, validation report, Python BOM, LeoCAD BOM comparison and a manifest tying artifacts to the model revision. `review --no-render` runs intermediate Python checks when previews are not needed. `./ldraw-agent doctor` reports local dependencies.

## Build your own model

Start an agent in this repository and give it your model idea. Agents supporting
the `AGENTS.md` convention load the [repository guidance](AGENTS.md) automatically;
other clients can read that file directly. Follow one sequence:

1. **Retrieve relevant lessons** with `./ldraw-agent knowledge search 'construction role'`; choose references that fit the brief and their documented limits.
2. **Assemble supports** in an ordinary [JSON plan](docs/agent/reference/plans.md), keeping required joints beside the geometry.
3. **Route services** between named fittings or mounted wheels.
4. **Add bodywork** on actual attachment points.
5. **Review and deliver** the editable MPD, source, matching BOMs and inspected images.

```sh
cp examples/workshop-machine/model.plan.json output/machine.plan.json
./ldraw-agent review output/machine.plan.json --views home front back top bottom
```

Edit the source and rerun with `--force`. For local repairs, use `review PLAN --section chain-drive.ldr --no-render`; final delivery still needs a rendered whole-model review. Keep visual notes outside generated review directories. Use Python when calculations or loops help; it emits the same plan dictionaries.

Technic joints and flexible routes use placement paths such as `controls/pump` or `conveyor/drive-axle`. Reordering parts keeps the intended joints; moving or rotating fittings updates their hose endpoints. An ordinary plan step can contain:

```json
{"id":"supply", "colour":0, "flex":{
  "method":"TECHNIC_PNEUMATIC_HOSE_BEVELED",
  "from":{"part":"controls/pump", "port":"outlet", "lead":22},
  "to":{"part":"controls/valve", "port":"inlet", "lead":22},
  "checks":{"min_radius":15, "tube_radius":5, "min_separation":10}
}}
```

Those limits belong to the pneumatic starter. `review` checks actual synthesized geometry and route interactions; unchanged inputs reuse raw synthesis while current checks run again. See [named authoring](docs/agent/guides/build-model.md) for supported ports, wheel ownership, inline contracts and repair diagnostics.

Existing plans, numeric external contracts, `.ldr` flex sources, prebuilt assets, Python helpers, CLI commands and Mecha handoff remain supported. `technic plan` still exports the original structural recipes; `build` still writes an MPD after checks pass. Successful checks provide evidence, not proof of physical strength, manufactured hose length or general motion correctness. The [verification record](docs/reports/authoring-simplification.md) includes compatibility checks, opened renders and independent trials.

| Need | Read |
|---|---|
| Agent workflow and task-specific guidance | [AGENTS.md](AGENTS.md) |
| Plan format, placements and Python API | [Plan guide](docs/agent/reference/plans.md) |
| Commands, review options and artifacts | [Tool reference](docs/agent/reference/cli.md) |
| Geometry checks and limitations | [Validation](docs/agent/reference/validation.md) |
| Flexible hoses, cables, bands, chains and treads | [Constraint authoring and repair](docs/agent/guides/flex.md), [LSynth integration](docs/agent/reference/flex.md), [examples](examples/flex/README.md) |
| Technic supports, equipment and industrial assemblies | [Design guide](docs/agent/guides/technic.md), [curated examples](examples/README.md) |
| Advanced mechanisms and animation | [Constraint workflow](docs/agent/guides/mechanisms.md), [gear-drive starter](examples/gear-drive/README.md), [mechanical parts catalog](docs/agent/reference/technic.md) |
| Studs Not On Top (SNOT) construction | [SNOT guide](docs/agent/guides/snot.md), [parrot core](examples/parrot/README.md), [cat details and joints](examples/cat/README.md), [submodel lessons](examples/submodel-atlas/README.md) |
| Spaceships: construction chosen for the brief | [Subject design guide](docs/agent/guides/design-models.md), [scoped spaceship lessons](examples/spaceship-atlas/README.md) |
| Reusable constructions and model examples | [Reference atlas](examples/reference-atlas/README.md), [submodel atlas](examples/submodel-atlas/README.md), `./ldraw-agent examples --help` |
| Visual inspection of source submodels | [Submodel inspection with ldraw-xray](docs/agent/guides/review-models.md), [submodel atlas](examples/submodel-atlas/README.md) |

Semantic reference search uses [jev-rerank](https://github.com/anteloc/jev-rerank) and a TypeSafe API key. It is optional: offline catalogs, examples and `discover search ... --engine fts` remain available. Follow the [availability check](docs/agent/reference/cli.md#check-jev-availability-before-searching) before using the service.

## Run the web app

The web app and Docker configuration live in the sibling [ldraw-nova-docker](https://github.com/anteloc/ldraw-nova-docker) repository. Install Git and Docker, then clone matching release tags side by side:

```sh
git clone --branch v0.6.0 https://github.com/anteloc/ldraw-nova.git
git clone --branch v0.6.0 https://github.com/anteloc/ldraw-nova-docker.git
cd ldraw-nova-docker
docker compose build
docker compose up -d
```

The first build needs about 5 GB of disk space. Open **http://localhost:8765**, or **https://localhost:8443** for VR on Meta Quest 3 (accept the self-signed certificate). Devices on your network can use the host computer's IP address. The app has no login; run it on a trusted network. Set `TYPESAFE_API_KEY` in the app's Settings for semantic search. Stop it with `docker compose down`.

The web app provides interactive 3D/VR views and conversation history. The local CLI also supports semantic GLB export for Blender through `./prepare-glb.sh --file MODEL.mpd MODEL.glb`, using the installed `mpd2glb.sh` converter. The editable MPD remains the authoritative model.

## Development

```sh
uv sync --locked --extra test
.venv/bin/python -m pytest -q
```

Tests use a miniature library plus the configured official library for integration cases. Integration tests skip explicitly when the official library is unavailable. Current checks do not certify material collision freedom, strength, motion or real part/colour availability. See the [verification records](docs/reports/tooling-verification-history.md) for tested coverage and historical results.

Further work includes model quality across more families, generation cost and speed, instruction-manual reconstruction, and VR handling/performance.

## Acknowledgements

I'd like to thank the following:

- [The LDraw Community](https://www.ldraw.org/)
- [LDView](https://tcobbs.github.io/ldview/)'s Travis Cobbs (@tcobbs), and contributors.
- [LeoCAD](https://github.com/leozide/leocad)'s Leonardo Zide (@leozide), and contributors.
- [LDCad](https://www.melkert.net/LDCad) and Shadow Library, Roland Melkert.
- [ldraw.rs](https://github.com/segfault87/ldraw.rs)'s Park Joon-Kyu (@segfault87), and contributors.
- [pyldraw3](https://github.com/hbmartin/pyldraw3)'s  Harold Martin (@hbmartin), and contributors.

... and thanks to all of the many other LDraw creators!

**NOTE:** For this work, I've used many LDraw models, libraries, tools, docs... from many sources.

There is a lot **amazing people** that generously contributed to this, even for **decades**, by **generously donating their finest work** to the public domain and open source community. 

If you think you should be included on this section, please **drop me an email!**

## Trademarks

**LEGO(R)** is a trademark of the **LEGO Group** of companies which does not sponsor, authorize or endorse this software.
