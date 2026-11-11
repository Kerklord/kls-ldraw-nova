#!/usr/bin/env bash
# shellcheck disable=SC2088,SC2015
# One-command installer for ldraw-nova on Linux, macOS and WSL (Windows).
#
#   curl -fsSL https://raw.githubusercontent.com/Kerklord/kls-ldraw-nova/master/install.sh | bash
#
# It checks each requirement and installs only what is missing, clones the
# repository, points it at your LDraw parts library, runs ./setup.sh and a
# test build. It is safe to run again. Run with --help for options.
set -euo pipefail

DEFAULT_REPO="https://github.com/Kerklord/kls-ldraw-nova.git"
ORIGINAL_REPO="https://github.com/anteloc/ldraw-nova.git"
XRAY_REPO="https://github.com/anteloc/ldraw-xray.git"
LEOCAD_MIN="23.03"   # first release with --export-csv, needed for BOM comparison

REPO=""
BRANCH=""
DIR=""
LDRAW=""
COPY_LDRAW=""        # yes | no | "" (ask)
WITH_LSYNTH=no
AI=""                # claude | gpt | both | none ("" = ask, default claude)
INSTALL_LEOCAD=yes
RUN_TEST=yes
ASSUME_YES=no
ACTION=install       # install | update | revert

usage() {
  cat <<'EOF'
Usage: install.sh [options]

  --repo URL|fork|original   Repository to clone (default: Kerklord fork)
  --branch NAME              Branch to check out (default: repository default)
  --dir PATH                 Where to put the repository (default: ~/ldraw-nova)
  --ldraw PATH               Your LDraw library folder (contains parts/) or complete.zip
  --copy-ldraw               Copy a library on a Windows drive (/mnt/...) into ~/ldraw (faster)
  --no-copy-ldraw            Use the library where it is
  --with-lsynth              Also install flexible-part support (hoses, chains)
  --ai claude|gpt|both|none  Which AI to install: Claude Code, OpenAI Codex (GPT), or both
  --no-claude                Same as --ai none
  --no-leocad                Skip installing LeoCAD
  --skip-test                Skip the bridge test build at the end
                             (exit code 3 means installed, but the test build failed)
  --update                   Update an existing install (in --dir) to the latest version
  --revert                   Go back to the version before the last --update
  -y, --yes                  Accept defaults instead of asking
  -h, --help                 Show this help
EOF
}

while [ $# -gt 0 ]; do
  case "$1" in
    --repo) REPO="$2"; shift 2 ;;
    --branch) BRANCH="$2"; shift 2 ;;
    --dir) DIR="$2"; shift 2 ;;
    --ldraw) LDRAW="$2"; shift 2 ;;
    --copy-ldraw) COPY_LDRAW=yes; shift ;;
    --no-copy-ldraw) COPY_LDRAW=no; shift ;;
    --with-lsynth) WITH_LSYNTH=yes; shift ;;
    --ai) AI="$2"; shift 2 ;;
    --no-claude) AI=none; shift ;;
    --no-leocad) INSTALL_LEOCAD=no; shift ;;
    --skip-test) RUN_TEST=no; shift ;;
    --update) ACTION=update; shift ;;
    --revert) ACTION=revert; shift ;;
    -y|--yes) ASSUME_YES=yes; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage >&2; exit 2 ;;
  esac
done

# ---------- output helpers ----------
if [ -t 1 ]; then B=$'\033[1m'; G=$'\033[32m'; Y=$'\033[33m'; R=$'\033[31m'; N=$'\033[0m'; else B=; G=; Y=; R=; N=; fi
step() { printf '\n%s==> %s%s\n' "$B" "$*" "$N"; }
ok()   { printf '  %s✓%s %s\n' "$G" "$N" "$*"; }
warn() { printf '  %s!%s %s\n' "$Y" "$N" "$*"; }
die()  { printf '\n%sError:%s %s\n' "$R" "$N" "$*" >&2; exit 1; }
have() { command -v "$1" >/dev/null 2>&1; }

# Read answers from the terminal even when this script arrives through a pipe.
TTY=""
if [ -r /dev/tty ] && { : </dev/tty; } 2>/dev/null; then TTY=/dev/tty; fi
ask() { # ask "Question" default -> echoes answer
  local q="$1" def="${2:-}" ans=""
  if [ "$ASSUME_YES" = yes ] || [ -z "$TTY" ]; then printf '%s\n' "$def"; return; fi
  printf '%s%s%s [%s]: ' "$B" "$q" "$N" "$def" >"$TTY"
  IFS= read -r ans <"$TTY" || true
  printf '%s\n' "${ans:-$def}"
}
confirm() { # confirm "Question" y|n
  local a; a=$(ask "$1 (y/n)" "${2:-y}")
  case "$a" in [Yy]*) return 0 ;; *) return 1 ;; esac
}
expand() { case "$1" in "~") printf '%s\n' "$HOME" ;; "~/"*) printf '%s/%s\n' "$HOME" "${1#\~/}" ;; *) printf '%s\n' "$1" ;; esac; }
version_ge() { [ "$(printf '%s\n%s\n' "$2" "$1" | sort -V | head -n1)" = "$2" ]; }

# ---------- platform ----------
OS=$(uname -s)
ARCH=$(uname -m)
IS_WSL=no
if [ "$OS" = Linux ] && grep -qi microsoft /proc/version 2>/dev/null; then IS_WSL=yes; fi
SUDO=""
if [ "$(id -u)" -ne 0 ]; then SUDO=sudo; fi
LOCAL_BIN="$HOME/.local/bin"
mkdir -p "$LOCAL_BIN"
case ":$PATH:" in *":$LOCAL_BIN:"*) ;; *) export PATH="$LOCAL_BIN:$PATH" ;; esac

PKG=""
if [ "$OS" = Darwin ]; then PKG=brew
elif have apt-get; then PKG=apt
elif have dnf; then PKG=dnf
elif have pacman; then PKG=pacman
fi

# ---------- update / revert an existing install ----------
# Versions are the repository's commits. Each update records the commit it left
# in .git/ldraw-nova-versions, so --revert can return to it (and --update forward again).
SELF_COPY="$HOME/.local/share/ldraw-nova/install.sh"
remember_self() { # keep a copy of this installer for the Update/Revert shortcuts
  local src="${BASH_SOURCE[0]:-}"
  if [ -n "$src" ] && [ -f "$src" ] && [ "$(cd "$(dirname "$src")" && pwd)/$(basename "$src")" != "$SELF_COPY" ]; then
    mkdir -p "$(dirname "$SELF_COPY")" && cp "$src" "$SELF_COPY"
  fi
}
refresh_project() { # rerun setup after the code changed
  local rc
  for rc in "$HOME/.profile" "$HOME/.bashrc" "$HOME/.zshrc"; do # our block sets LDRAW_DIR
    [ -n "${LDRAW_DIR:-}" ] && break
    [ -f "$rc" ] && eval "$(sed -n '/^# >>> ldraw-nova >>>$/,/^# <<< ldraw-nova <<<$/p' "$rc")"
  done
  [ -n "${LDRAW_DIR:-}" ] || die "LDRAW_DIR is not set. Run: export LDRAW_DIR=/path/to/ldraw, then try again."
  local args=()
  [ "$WITH_LSYNTH" = yes ] && args+=(--with-lsynth)
  if ./setup.sh "${args[@]}" >/tmp/ldraw-nova-setup.log 2>&1; then ok "Python environment and parts index refreshed"
  else tail -n 20 /tmp/ldraw-nova-setup.log; die "setup.sh failed (full log: /tmp/ldraw-nova-setup.log)"; fi
}
if [ "$ACTION" != install ]; then
  DIR=$(expand "${DIR:-~/ldraw-nova}")
  [ -d "$DIR/.git" ] || die "No ldraw-nova install found in $DIR. Pass --dir."
  cd "$DIR"
  HISTORY=.git/ldraw-nova-versions
  if [ -n "$(git status --porcelain --untracked-files=no)" ]; then
    die "You have changed files in $DIR. Commit or discard them first, so nothing is lost."
  fi
  current=$(git rev-parse HEAD)
  if [ "$ACTION" = update ]; then
    step "Updating $DIR"
    git fetch -q origin || die "Could not reach $(git remote get-url origin)."
    upstream=$(git rev-parse '@{u}' 2>/dev/null) || die "The current branch does not track a remote branch."
    if [ "$upstream" = "$current" ]; then ok "Already up to date ($(git rev-parse --short HEAD))"; exit 0; fi
    git merge -q --ff-only "$upstream" || die "Your copy has its own commits; update it with git instead."
    echo "$current" >>"$HISTORY"
    ok "Updated $(git rev-parse --short "$current") -> $(git rev-parse --short HEAD)"
    git log --oneline "$current..HEAD" | head -n 10 | sed 's/^/    /'
    [ -f install.sh ] && cp install.sh "$SELF_COPY" 2>/dev/null || true
    refresh_project
    ok "Done. To undo, run the Revert shortcut or: install.sh --revert --dir $DIR"
  else
    step "Reverting $DIR"
    [ -s "$HISTORY" ] || die "There is no earlier version to go back to. One is recorded each time you update."
    previous=$(tail -n 1 "$HISTORY")
    git cat-file -e "$previous^{commit}" 2>/dev/null || die "The recorded version $previous is no longer available."
    git reset -q --keep "$previous" || die "Could not switch back to $(git rev-parse --short "$previous")."
    sed -i.bak '$d' "$HISTORY" && rm -f "$HISTORY.bak"
    ok "Switched back $(git rev-parse --short "$current") -> $(git rev-parse --short HEAD)"
    refresh_project
    ok "Done. The next update brings you forward again."
  fi
  exit 0
fi
remember_self

printf '%sldraw-nova installer%s\n' "$B" "$N"
printf '  System: %s %s%s, package manager: %s\n' "$OS" "$ARCH" "$([ "$IS_WSL" = yes ] && echo ' (WSL)')" "${PKG:-none found}"

# ---------- 1. system packages ----------
step "Checking system packages"
if [ "$PKG" = brew ] && ! have brew; then
  die "Homebrew is required on macOS. Install it from https://brew.sh, then run this again."
fi
[ -n "$PKG" ] || die "No supported package manager found (apt, dnf, pacman or Homebrew)."

need=()
have git || need+=(git)
have curl || need+=(curl)
have pdftotext || need+=(poppler)
have unzip || need+=(unzip)
if [ "$OS" = Linux ] && [ "$INSTALL_LEOCAD" = yes ]; then
  have xvfb-run || need+=(xvfb)
fi

if [ ${#need[@]} -eq 0 ]; then
  ok "git, curl, pdftotext and unzip are present"
else
  echo "  Installing: ${need[*]}"
  case "$PKG" in
    apt)
      pkgs=()
      for p in "${need[@]}"; do case "$p" in poppler) pkgs+=(poppler-utils) ;; xvfb) pkgs+=(xvfb xauth) ;; *) pkgs+=("$p") ;; esac; done
      $SUDO apt-get update -qq
      $SUDO env DEBIAN_FRONTEND=noninteractive apt-get install -y -qq "${pkgs[@]}" ;;
    dnf)
      pkgs=()
      for p in "${need[@]}"; do case "$p" in poppler) pkgs+=(poppler-utils) ;; xvfb) pkgs+=(xorg-x11-server-Xvfb) ;; *) pkgs+=("$p") ;; esac; done
      $SUDO dnf install -y -q "${pkgs[@]}" ;;
    pacman)
      pkgs=()
      for p in "${need[@]}"; do case "$p" in xvfb) pkgs+=(xorg-server-xvfb) ;; *) pkgs+=("$p") ;; esac; done
      $SUDO pacman -S --needed --noconfirm "${pkgs[@]}" ;;
    brew)
      pkgs=()
      for p in "${need[@]}"; do pkgs+=("$p"); done
      brew install "${pkgs[@]}" ;;
  esac
  ok "Installed ${need[*]}"
fi

# ---------- 2. uv + Python 3.12 ----------
step "Checking uv and Python 3.12"
if ! have uv; then
  if ! curl -LsSf https://astral.sh/uv/install.sh | env UV_NO_MODIFY_PATH=1 sh >/dev/null; then
    die "Could not download uv from astral.sh. Check your internet connection, or install it from https://docs.astral.sh/uv/ and run this again."
  fi
  have uv || die "uv did not install. See https://docs.astral.sh/uv/ and run this again."
fi
ok "uv $(uv --version | awk '{print $2}')"
uv python find 3.12 >/dev/null 2>&1 || uv python install 3.12 >/dev/null 2>&1 || die "uv could not install Python 3.12."
ok "Python $("$(uv python find 3.12)" --version 2>&1 | awk '{print $2}')"

# ---------- 3. LeoCAD ----------
leocad_version() { # prints e.g. 23.03, or nothing
  have leocad || return 0
  leocad --version 2>/dev/null | grep -Eo '[0-9]+\.[0-9]+' | head -n1 || true
}

install_leocad_linux() {
  local arch_re api url dest
  case "$ARCH" in x86_64|amd64) arch_re='x86_64|amd64' ;; aarch64|arm64) arch_re='aarch64|arm64' ;; *) arch_re="$ARCH" ;; esac
  # Newest first; take the first release that ships a Linux AppImage.
  api=$(curl -fsSL "https://api.github.com/repos/leozide/leocad/releases?per_page=10") || return 1
  url=$(printf '%s\n' "$api" | grep -Eo '"browser_download_url": *"[^"]+"' | sed -E 's/.*"(https[^"]+)"/\1/' \
        | grep -i 'appimage$' | grep -Ei "$arch_re" | head -n1 || true)
  if [ -z "$url" ]; then # some releases omit the arch from the name
    url=$(printf '%s\n' "$api" | grep -Eo '"browser_download_url": *"[^"]+"' | sed -E 's/.*"(https[^"]+)"/\1/' \
          | grep -i 'appimage$' | head -n1 || true)
  fi
  [ -n "$url" ] || return 1
  dest="$HOME/.local/opt/leocad"
  rm -rf "$dest"; mkdir -p "$dest"
  echo "  Downloading $(basename "$url")"
  curl -fL# -o "$dest/LeoCAD.AppImage" "$url" || return 1
  chmod +x "$dest/LeoCAD.AppImage"
  # Extract instead of mounting, so FUSE is not needed (WSL often lacks it).
  (cd "$dest" && ./LeoCAD.AppImage --appimage-extract >/dev/null) || return 1
  if [ "$PKG" = apt ]; then # runtime libraries the bundled Qt expects
    $SUDO env DEBIAN_FRONTEND=noninteractive apt-get install -y -qq libgl1 libegl1 libopengl0 libfontconfig1 \
      libxkbcommon-x11-0 libxcb-cursor0 libxcb-icccm4 libxcb-image0 libxcb-keysyms1 libxcb-randr0 \
      libxcb-render-util0 libxcb-shape0 libxcb-xinerama0 libxcb-xkb1 libdbus-1-3 >/dev/null 2>&1 || true
  fi
  write_leocad_wrapper "$dest/squashfs-root/AppRun"
}

write_leocad_wrapper() { # $1 = real LeoCAD executable
  cat >"$LOCAL_BIN/leocad" <<EOF
#!/bin/sh
# Installed by ldraw-nova install.sh. Batch renders run on a virtual display
# when there is no screen, so previews work in WSL, SSH and CI.
BIN="$1"
for a in "\$@"; do
  case "\$a" in -i|--image|-csv|--export-csv|-v|--version|-\?|--help|-obj|-3ds|-dae|-html|--export-*) BATCH=1 ;; esac
done
if [ -n "\${BATCH:-}" ] && command -v xvfb-run >/dev/null 2>&1 && [ "\$(uname -s)" = Linux ]; then
  exec xvfb-run -a "\$BIN" "\$@"
fi
exec "\$BIN" "\$@"
EOF
  chmod +x "$LOCAL_BIN/leocad"
  hash -r 2>/dev/null || true
}

step "Checking LeoCAD (renders previews, needs $LEOCAD_MIN or newer)"
if [ "$INSTALL_LEOCAD" = no ]; then
  warn "Skipped (--no-leocad). Reviews will fail at the render step until LeoCAD is installed."
else
  lv=$(leocad_version)
  if [ -n "$lv" ] && version_ge "$lv" "$LEOCAD_MIN"; then
    ok "LeoCAD $lv"
  else
    [ -n "$lv" ] && warn "LeoCAD $lv is too old (no CSV parts-list export); installing the latest release"
    if [ "$OS" = Darwin ]; then
      [ -d /Applications/LeoCAD.app ] || brew install --cask leocad
      write_leocad_wrapper /Applications/LeoCAD.app/Contents/MacOS/LeoCAD
    else
      install_leocad_linux || die "Could not download LeoCAD from https://github.com/leozide/leocad/releases.
  Download the Linux AppImage there, then run: install.sh --no-leocad, and put it on PATH as 'leocad'."
    fi
    lv=$(leocad_version)
    if [ -n "$lv" ] && version_ge "$lv" "$LEOCAD_MIN"; then ok "LeoCAD $lv"; else warn "LeoCAD installed but its version could not be confirmed"; fi
  fi
fi

# ---------- 4. AI assistant(s) ----------
if [ -z "$AI" ]; then
  echo
  echo "  Which AI should build your models?"
  echo "  1) Claude (Claude Code; needs a Claude plan or an Anthropic API key)"
  echo "  2) GPT (OpenAI Codex; needs a ChatGPT plan or an OpenAI API key)"
  echo "  3) Both"
  case "$(ask "Choose 1, 2 or 3" 1)" in 2|gpt) AI=gpt ;; 3|both) AI=both ;; *) AI=claude ;; esac
fi
case "$AI" in claude|gpt|both|none) ;; *) die "--ai must be claude, gpt, both or none" ;; esac

install_node() { # Node.js LTS into ~/.local/opt/node, no sudo needed
  local os arch ver dir
  case "$OS" in Linux) os=linux ;; Darwin) os=darwin ;; esac
  case "$ARCH" in x86_64|amd64) arch=x64 ;; aarch64|arm64) arch=arm64 ;; *) return 1 ;; esac
  local index
  index=$(curl -fsSL https://nodejs.org/dist/index.json) || return 1
  ver=$(printf '%s\n' "$index" | grep -Eo '"version":"v[0-9.]+"[^}]*"lts":"[^"]+"' | grep -Eo 'v[0-9]+\.[0-9]+\.[0-9]+' | head -n1 || true)
  [ -n "$ver" ] || return 1
  dir="$HOME/.local/opt/node"
  rm -rf "$dir"; mkdir -p "$dir"
  echo "  Downloading Node.js $ver (needed by Codex)"
  curl -fsSL "https://nodejs.org/dist/$ver/node-$ver-$os-$arch.tar.gz" | tar -xz -C "$dir" --strip-components=1 || return 1
  ln -sf "$dir/bin/node" "$dir/bin/npm" "$dir/bin/npx" "$LOCAL_BIN/"
  hash -r 2>/dev/null || true
}

if [ "$AI" = claude ] || [ "$AI" = both ]; then
  step "Checking Claude Code"
  if have claude; then
    ok "Claude Code $(claude --version 2>/dev/null | head -n1)"
  else
    curl -fsSL https://claude.ai/install.sh | bash >/dev/null 2>&1 || die "Claude Code did not install. See https://docs.claude.com/en/docs/claude-code/setup"
    hash -r 2>/dev/null || true
    if have claude; then ok "Claude Code installed"; else warn "Claude Code installed; open a new terminal for the 'claude' command"; fi
  fi
fi
if [ "$AI" = gpt ] || [ "$AI" = both ]; then
  step "Checking OpenAI Codex (GPT)"
  if have codex; then
    ok "Codex $(codex --version 2>/dev/null | head -n1)"
  else
    nv=$(node --version 2>/dev/null | tr -d v || true)
    if [ -z "$nv" ] || ! version_ge "$nv" 18; then install_node || die "Could not install Node.js from nodejs.org."; fi
    # Install into ~/.local so no sudo is needed.
    npm install -g --silent --prefix "$HOME/.local" @openai/codex >/dev/null 2>&1 || die "npm could not install @openai/codex."
    hash -r 2>/dev/null || true
    have codex || die "Codex installed but 'codex' is not on PATH."
    ok "Codex $(codex --version 2>/dev/null | head -n1)"
  fi
fi
[ "$AI" = none ] && warn "No AI assistant installed (--ai none)"

# ---------- 5. Repository ----------
step "Getting the ldraw-nova repository"
if [ -z "$REPO" ]; then
  echo "  1) Your fork:    $DEFAULT_REPO"
  echo "  2) The original: $ORIGINAL_REPO"
  echo "  Or paste any other git URL."
  choice=$(ask "Which repository?" 1)
  case "$choice" in 1|fork) REPO=$DEFAULT_REPO ;; 2|original) REPO=$ORIGINAL_REPO ;; *) REPO=$choice ;; esac
fi
case "$REPO" in fork) REPO=$DEFAULT_REPO ;; original) REPO=$ORIGINAL_REPO ;; esac
[ -n "$DIR" ] || DIR=$(ask "Install folder" "~/ldraw-nova")
DIR=$(expand "$DIR")
case "$DIR" in /mnt/[a-z]/*) warn "A folder on a Windows drive is much slower in WSL; ~/ldraw-nova is recommended" ;; esac

if [ -d "$DIR/.git" ]; then
  origin=$(git -C "$DIR" remote get-url origin 2>/dev/null || true)
  if [ "${origin%.git}" != "${REPO%.git}" ]; then
    die "$DIR already holds a different repository ($origin). Choose another --dir."
  fi
  if [ -n "$(git -C "$DIR" status --porcelain --untracked-files=no)" ]; then
    warn "Local changes in $DIR; not updating it"
  else
    git -C "$DIR" pull --ff-only -q && ok "Updated $DIR"
  fi
elif [ -e "$DIR" ] && [ -n "$(ls -A "$DIR" 2>/dev/null)" ]; then
  die "$DIR exists and is not empty. Choose another --dir."
else
  mkdir -p "$(dirname "$DIR")"
  echo "  Cloning $REPO (about 12,000 files, a few minutes)"
  git clone -q ${BRANCH:+--branch "$BRANCH"} "$REPO" "$DIR"
  ok "Cloned into $DIR"
fi
if [ -n "$BRANCH" ] && [ "$(git -C "$DIR" rev-parse --abbrev-ref HEAD)" != "$BRANCH" ]; then
  git -C "$DIR" fetch -q origin "$BRANCH" && git -C "$DIR" checkout -q "$BRANCH" && ok "On branch $BRANCH"
fi
if [ ! -f "$SELF_COPY" ]; then # e.g. when run through curl | bash
  mkdir -p "$(dirname "$SELF_COPY")"
  if [ -f "$DIR/install.sh" ]; then cp "$DIR/install.sh" "$SELF_COPY"
  else curl -fsSL https://raw.githubusercontent.com/Kerklord/kls-ldraw-nova/master/install.sh -o "$SELF_COPY" \
    || warn "Could not save a copy of the installer; updates will need: curl ... | bash -s -- --update"; fi
fi

XRAY_DIR="$(dirname "$DIR")/ldraw-xray"
if [ -d "$XRAY_DIR/.git" ]; then
  ok "ldraw-xray guide present at $XRAY_DIR"
elif [ -e "$XRAY_DIR" ]; then
  warn "$XRAY_DIR exists but is not a git checkout; leaving it alone"
else
  git clone -q --depth 1 "$XRAY_REPO" "$XRAY_DIR" && ok "Cloned ldraw-xray next to it (AGENTS.md reads its README)"
fi

# ---------- 6. LDraw library ----------
step "Locating your LDraw parts library"
valid_lib() { [ -d "$1/parts" ] && [ -d "$1/p" ]; }
find_lib() { # accepts the library folder, its parent, or complete.zip
  local p; p=$(expand "$1")
  if valid_lib "$p"; then printf '%s\n' "$p"; elif valid_lib "$p/ldraw"; then printf '%s\n' "$p/ldraw"; else return 1; fi
}
if [ -z "$LDRAW" ]; then
  for c in "${LDRAW_DIR:-}" "$HOME/ldraw" "$HOME/LDraw" /usr/share/ldraw /usr/share/ldraw-parts; do
    [ -n "$c" ] && valid_lib "$c" && { LDRAW=$c; break; }
  done
  LDRAW=$(ask "LDraw folder (contains parts/ and p/) or complete.zip" "${LDRAW:-~/ldraw}")
fi
LDRAW=$(expand "$LDRAW")

if [ -f "$LDRAW" ] && [ "${LDRAW##*.}" = zip ]; then
  dest="$HOME"
  echo "  Unzipping $(basename "$LDRAW") into $dest/ldraw"
  unzip -q -o "$LDRAW" -d "$dest"
  LDRAW="$dest/ldraw"
fi
LIB=$(find_lib "$LDRAW") || die "No parts/ and p/ folders found in $LDRAW.
  Point --ldraw at the unzipped library folder (the one containing parts/), or at complete.zip
  from https://library.ldraw.org/updates"

case "$LIB" in
  /mnt/[a-z]/*)
    if [ -z "$COPY_LDRAW" ]; then
      if confirm "Your library is on a Windows drive, which is slow from WSL. Copy it to ~/ldraw?" y; then COPY_LDRAW=yes; else COPY_LDRAW=no; fi
    fi
    if [ "$COPY_LDRAW" = yes ]; then
      if [ -d "$HOME/ldraw/parts" ]; then
        warn "$HOME/ldraw already exists; using it as is"
      else
        echo "  Copying (this can take a few minutes)"
        mkdir -p "$HOME/ldraw" && cp -a "$LIB/." "$HOME/ldraw/"
      fi
      LIB="$HOME/ldraw"
    fi ;;
esac
[ -f "$LIB/LDConfig.ldr" ] || warn "$LIB has no LDConfig.ldr; colour lookups may fail"
ok "LDraw library: $LIB ($(find "$LIB/parts" -maxdepth 1 -name '*.dat' | wc -l | tr -d ' ') parts)"
export LDRAW_DIR="$LIB"

# Remember LDRAW_DIR and ~/.local/bin for new terminals.
persist() {
  local rc="$1" tmp
  [ -f "$rc" ] || return 0
  tmp=$(mktemp)
  awk '/^# >>> ldraw-nova >>>$/{skip=1} !skip{print} /^# <<< ldraw-nova <<<$/{skip=0}' "$rc" >"$tmp"
  {
    echo '# >>> ldraw-nova >>>'
    printf 'export LDRAW_DIR=%q\n' "$LIB"
    # shellcheck disable=SC2016
    echo 'case ":$PATH:" in *":$HOME/.local/bin:"*) ;; *) export PATH="$HOME/.local/bin:$PATH" ;; esac'
    echo '# <<< ldraw-nova <<<'
  } >>"$tmp"
  cat "$tmp" >"$rc" && rm -f "$tmp"
}
[ -f "$HOME/.bashrc" ] || touch "$HOME/.bashrc"
[ "$OS" = Darwin ] && { [ -f "$HOME/.zshrc" ] || touch "$HOME/.zshrc"; }
for rc in "$HOME/.bashrc" "$HOME/.zshrc" "$HOME/.profile"; do persist "$rc"; done
ok "Saved LDRAW_DIR in your shell profile"

# ---------- 7. Project setup ----------
step "Running ./setup.sh"
cd "$DIR"
if [ "$WITH_LSYNTH" = yes ]; then ./setup.sh --with-lsynth >/tmp/ldraw-nova-setup.log 2>&1 || { tail -n 30 /tmp/ldraw-nova-setup.log; die "setup.sh failed (full log: /tmp/ldraw-nova-setup.log)"; }
else ./setup.sh >/tmp/ldraw-nova-setup.log 2>&1 || { tail -n 30 /tmp/ldraw-nova-setup.log; die "setup.sh failed (full log: /tmp/ldraw-nova-setup.log)"; }
fi
ok "Python environment and parts index ready"

doctor=$(./ldraw-agent doctor 2>/dev/null || true)
check_tool() { printf '%s' "$doctor" | grep -q "\"$1\": \"/" ; }
printf '%s' "$doctor" | grep -q '"library_present": true' && ok "doctor: library found" || warn "doctor: library not found"
check_tool pdftotext && ok "doctor: pdftotext found" || warn "doctor: pdftotext missing"
check_tool leocad && ok "doctor: leocad found" || warn "doctor: leocad missing"

# ---------- 8. Test build ----------
TEST_STATUS="skipped"
if [ "$RUN_TEST" = yes ]; then
  step "Test build: examples/plan-basics/bridge.plan.json"
  if ./ldraw-agent review examples/plan-basics/bridge.plan.json --outdir output/install-check --force >/tmp/ldraw-nova-test.json 2>&1; then
    TEST_STATUS="passed"
    ok "Built, checked and rendered. Pictures: $DIR/output/install-check/previews/"
  else
    TEST_STATUS="failed"
    warn "The test review did not pass. Details: /tmp/ldraw-nova-test.json"
    grep -Eo '"(error|failed_stage)": "[^"]*"' /tmp/ldraw-nova-test.json | head -n 3 | sed 's/^/    /' || true
  fi
fi

# ---------- Done ----------
step "Done"
echo "  Repository:     $DIR"
echo "  LDraw library:  $LIB"
echo "  Test build:     $TEST_STATUS"
if [ "$IS_WSL" = yes ]; then
  have wslpath && echo "  In Windows Explorer: $(wslpath -w "$DIR")"
fi
echo
case "$AI" in
  claude) echo "  Start building:  ${B}cd $DIR && claude${N}" ;;
  gpt)    echo "  Start building:  ${B}cd $DIR && codex${N}" ;;
  both)   echo "  Start building:  ${B}cd $DIR && claude${N}   (or ${B}codex${N} for GPT)" ;;
esac
echo "  Then describe the model you want. Open a new terminal first if the command is not found."
# Exit 3 = installed but the test build failed (1 = installer error, 2 = bad option).
[ "$TEST_STATUS" = failed ] && exit 3
exit 0
