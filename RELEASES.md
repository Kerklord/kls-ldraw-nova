# Web app releases

The web app (chat page, 3D viewer, VR) and the tools must come from the same
release. A newer tools version can break an older web app: when upstream moved
the `docs/agent/*.md` guides, every chat failed with
`FileNotFoundError: Provider request failed`.

So this repository ships both under one tag:

- **`release/0.6`** is the release branch. It holds the tools at upstream
  `anteloc/ldraw-nova` v0.6.0 plus the web app in `web-app/` (upstream
  `anteloc/ldraw-nova-docker` v0.6.0 with KL's changes, listed at the top of
  `web-app/README.md`).
- **Release tags** (`v0.6.1`, `v0.6.2`, ...) are made on that branch. The
  Windows installer and the *Update LDRAW-NOVA* shortcut install the newest tag:
  the tools into `ldraw-nova\` and `web-app\` into `ldraw-nova-docker\`, side by
  side, then build the Docker image.
- **`master`** keeps the installer, the B-Launcher add-on and the newest tools
  for terminal mode. It has no `web-app/` folder, so don't tag it.

## Changing the web app

1. Open a PR into `release/0.6` that edits `web-app/`. Add a line to the change
   list in `web-app/README.md`.
2. After merging, tag the merge commit with the next version, such as
   `git tag v0.6.2 origin/release/0.6 && git push origin v0.6.2`.
3. Click *Update LDRAW-NOVA*. A build that fails changes nothing.

## Taking in a new upstream release

When upstream tags a new version (say v0.7.0) in both `anteloc/ldraw-nova` and
`anteloc/ldraw-nova-docker`:

1. Create `release/0.7` from upstream ldraw-nova v0.7.0.
2. Copy ldraw-nova-docker v0.7.0 into `web-app/`, leaving out `data/chats` and
   `data/output` (sample runs), and re-apply KL's changes from `release/0.6`.
3. Tag it `v0.7.1`. Never push anything to the upstream repositories.

The installer still accepts `-DockerRepo <url>` to take the web app from a
separate repository instead.
