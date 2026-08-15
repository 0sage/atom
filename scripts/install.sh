#!/bin/sh
# atom installer: prerequisites -> uv -> atom. Nothing else.
#
# POSIX sh on purpose. The minimal images this targets (Alpine, container
# rootfs) have no bash, and the whole point is to run before anything is set up.
#
#   ./install.sh                      install for the invoking user
#   ./install.sh --ref v0.3.5         pin a tag, branch, or commit
#   ./install.sh --with-api           include the [api] extra
#   ./install.sh --check              report what is missing, change nothing
#
# This script provisions the host and stops. It does not run `atom onboard` and
# does not register a service: onboarding writes config and a workspace, and a
# service starts a long-running process that talks to the network. Those are
# decisions for the operator to make explicitly, not side effects of an install.
# It prints the exact next commands when it finishes.

set -eu

REPO="https://github.com/0sage/atom.git"
REF="main"
CHECK_ONLY=0
WITH_API=0

say()  { printf '%s\n' "$*"; }
step() { printf '\n==> %s\n' "$*"; }
warn() { printf 'warning: %s\n' "$*" >&2; }
die()  { printf 'error: %s\n' "$*" >&2; exit 1; }

while [ $# -gt 0 ]; do
    case "$1" in
        --ref)      REF="${2:?--ref needs a value}"; shift 2 ;;
        --ref=*)    REF="${1#*=}"; shift ;;
        --check)    CHECK_ONLY=1; shift ;;
        --with-api) WITH_API=1; shift ;;
        -h|--help)  sed -n '2,16p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *)          die "unknown option: $1 (try --help)" ;;
    esac
done

have() { command -v "$1" >/dev/null 2>&1; }

# uv installs into ~/.local/bin, which is often absent from PATH in a
# non-interactive shell. Prepend it before anything looks for uv or atom, or
# --check reports "would install uv" on a host that already has it.
UV_BIN="${HOME}/.local/bin"
case ":${PATH}:" in
    *":${UV_BIN}:"*) UV_BIN_ON_PATH=1 ;;
    *) UV_BIN_ON_PATH=0; PATH="${UV_BIN}:${PATH}"; export PATH ;;
esac

# ---------------------------------------------------------------- inspect host
step "Inspecting host"

OS_NAME="unknown"; OS_VERSION=""
if [ -r /etc/os-release ]; then
    # shellcheck disable=SC1091
    . /etc/os-release
    OS_NAME="${ID:-unknown}"
    OS_VERSION="${VERSION_ID:-}"
fi

UID_NOW="$(id -u)"
IS_ROOT=0
[ "$UID_NOW" -eq 0 ] && IS_ROOT=1

# The package manager decides how we install prerequisites.
PKG=""
for candidate in apt-get dnf yum apk pacman zypper; do
    if have "$candidate"; then PKG="$candidate"; break; fi
done

# How we escalate for package installs. Root needs nothing; anyone else needs
# sudo, and if it is absent we say so rather than failing deep in a package step.
SUDO=""
if [ "$IS_ROOT" -eq 0 ] && have sudo; then
    SUDO="sudo"
fi

# Reported only so the closing hint can name the right service commands. This
# script never touches an init system.
INIT="unknown"
if [ -d /run/systemd/system ]; then
    INIT="systemd"
elif have rc-service; then
    INIT="openrc"
elif [ "$(uname -s)" = "Darwin" ]; then
    INIT="launchd"
fi

say "  os:      ${OS_NAME} ${OS_VERSION}"
say "  arch:    $(uname -m)"
say "  user:    $(id -un) (uid ${UID_NOW})"
say "  pkg:     ${PKG:-none found}"
say "  init:    ${INIT}"

# ---------------------------------------------------------- check prerequisites
step "Checking prerequisites"

# curl and tar are needed to fetch and unpack uv; git to install from the repo.
# python3 is only a convenience -- uv fetches its own interpreter when the
# host's is too old (CentOS 9 ships 3.9, below atom's floor).
MISSING=""
for tool in curl tar git; do
    if have "$tool"; then
        say "  ok:      ${tool}"
    else
        say "  MISSING: ${tool}"
        MISSING="${MISSING} ${tool}"
    fi
done

if have python3; then
    say "  ok:      python3 ($(python3 -V 2>&1))"
else
    say "  note:    python3 absent; uv will fetch its own"
fi

if [ "$CHECK_ONLY" -eq 1 ]; then
    step "Check only; nothing was changed"
    [ -n "$MISSING" ] && say "Would install packages:${MISSING}"
    have uv || say "Would install: uv"
    have atom && say "atom already present: $(atom --version 2>&1)" \
              || say "Would install: atom (${REF})"
    exit 0
fi

# ------------------------------------------------------- install prerequisites
if [ -n "$MISSING" ]; then
    step "Installing prerequisites:${MISSING}"
    [ -z "$PKG" ] && die "no supported package manager found; install${MISSING} manually"
    if [ "$IS_ROOT" -eq 0 ] && [ -z "$SUDO" ]; then
        die "need root or sudo to install${MISSING}; install them manually and re-run"
    fi
    # shellcheck disable=SC2086
    case "$PKG" in
        apt-get) $SUDO apt-get update -qq && DEBIAN_FRONTEND=noninteractive $SUDO apt-get install -y -qq $MISSING ca-certificates ;;
        dnf)     $SUDO dnf install -y -q $MISSING ;;
        yum)     $SUDO yum install -y -q $MISSING ;;
        apk)     $SUDO apk add --no-cache $MISSING ca-certificates ;;
        pacman)  $SUDO pacman -Sy --noconfirm $MISSING ;;
        zypper)  $SUDO zypper --non-interactive install $MISSING ;;
    esac
    for tool in $MISSING; do
        have "$tool" || die "installed packages but ${tool} is still missing"
    done
    say "  installed"
fi

# ------------------------------------------------------------------ install uv
if have uv; then
    step "uv already present: $(uv --version)"
else
    step "Installing uv"
    curl -fsSL https://astral.sh/uv/install.sh | sh >/dev/null 2>&1 \
        || die "uv install failed; see https://docs.astral.sh/uv/"
fi

# PATH already includes ${UV_BIN} (set during inspection), so a freshly
# installed uv is visible here without re-checking.
have uv || die "uv is installed but not on PATH; add ${UV_BIN} to PATH"

# ---------------------------------------------------------------- install atom
step "Installing atom from ${REPO}@${REF}"

TARGET="git+${REPO}@${REF}"
[ "$WITH_API" -eq 1 ] && TARGET="${TARGET}[api]"

uv tool install --force "$TARGET" >/dev/null 2>&1 \
    || die "atom install failed; retry without -q to see why:
  uv tool install \"${TARGET}\""

have atom || die "atom installed but not on PATH; add ${UV_BIN} to PATH"
say "  $(atom --version 2>&1)"

# ------------------------------------------------------------------ persist PATH
# Everything above found atom only because line 49 prepended UV_BIN to this
# process's PATH. That does not outlive the script, so without this step the
# install "succeeds" and then the next shell reports `atom: command not found`.
#
# `uv tool update-shell` is the documented cure and cannot do it from here: the
# prepend already happened, so uv sees UV_BIN in PATH and reports "already in
# PATH" without writing anything. Removing the prepend does not help either —
# uv then needs $SHELL to pick a profile, and a piped `curl | sh` has none, so
# it fails with "the current shell could not be determined". Both were verified
# on a fresh Debian container. Hence: write the line ourselves.
if [ "$UV_BIN_ON_PATH" -eq 0 ]; then
    step "Adding ${UV_BIN} to PATH"

    # Which file a login shell actually reads differs per shell, and root on
    # Debian gets no ~/.local/bin block the way a normal user does. Write to the
    # rc file for the login shell when we can name it, and always to ~/.profile,
    # which sh and bash both read when it exists.
    RC_FILES="${HOME}/.profile"
    case "${SHELL:-}" in
        */zsh)  RC_FILES="${RC_FILES} ${HOME}/.zshrc" ;;
        */bash) RC_FILES="${RC_FILES} ${HOME}/.bashrc" ;;
        *)      [ -f "${HOME}/.bashrc" ] && RC_FILES="${RC_FILES} ${HOME}/.bashrc" ;;
    esac

    PATH_LINE="export PATH=\"${UV_BIN}:\$PATH\""
    for rc in $RC_FILES; do
        # Idempotent: re-running the installer must not stack duplicate lines.
        if [ -f "$rc" ] && grep -qF "$PATH_LINE" "$rc" 2>/dev/null; then
            say "  already set: ${rc}"
            continue
        fi
        if printf '\n# added by atom installer: uv installs executables here\n%s\n' \
               "$PATH_LINE" >> "$rc" 2>/dev/null; then
            say "  updated: ${rc}"
        else
            warn "could not write ${rc}; add this line yourself:"
            warn "  ${PATH_LINE}"
        fi
    done
fi

# ------------------------------------------------------------------- next steps
step "Installed"

if [ "$UV_BIN_ON_PATH" -eq 0 ]; then
    say "${UV_BIN} was added to your PATH, but only for shells started from now on."
    say "In this one, either open a new shell or run:"
    say ""
    say "  export PATH=\"${UV_BIN}:\$PATH\""
    say ""
fi

say "atom is installed. Nothing is configured and no service is running."
say ""
say "Next, in this order:"
say ""
say "  1. atom onboard --wizard      # pick a provider and model"
say "  2. atom status                # confirm 'Agent: ✓'"
say "  3. atom agent -m \"Hello!\"     # prove one real reply"
say ""

if [ -f "${HOME}/.atom/config.json" ]; then
    say "(a config already exists at ${HOME}/.atom/config.json; onboard leaves it alone"
    say " unless you pass --force, and 'atom onboard --refresh' adds new fields)"
    say ""
fi

say "Only once step 3 works, register the always-on gateway:"
say ""
case "$INIT" in
    systemd)
        if [ "$IS_ROOT" -eq 1 ]; then
            say "  atom gateway install-service          # root -> /etc/systemd/system"
            say "  systemctl status atom-gateway"
        else
            say "  atom gateway install-service          # -> ~/.config/systemd/user"
            say "  systemctl --user status atom-gateway"
            say ""
            say "  User services stop at logout. To survive it:"
            say "    sudo loginctl enable-linger $(id -un)"
        fi
        ;;
    openrc)
        say "  install-service has no OpenRC backend and will refuse here."
        say "  Use the init script in docs/deployment.md ('Hosts Without systemd')."
        say "  To try it right now without installing anything:"
        say "    atom gateway --foreground"
        ;;
    launchd)
        say "  atom gateway install-service          # -> ~/Library/LaunchAgents"
        say "  launchctl list | grep ai.atom.gateway"
        ;;
    *)
        say "  No supported init system detected (${INIT})."
        say "  Run it directly instead: atom gateway --foreground"
        ;;
esac
say ""
say "The gateway refuses to start without a provider, and systemd retries it"
say "forever, so finish steps 1-3 before registering a service."
