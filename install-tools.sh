#!/usr/bin/env bash
# Install the external tools stegray drives, on the host rather than in the
# container image. Detects the OS and its package manager, installs what the
# distribution packages, and builds or downloads the rest (the same pinned
# versions as the Dockerfile) into PREFIX/bin. Tools already on PATH are left
# alone, so it is safe to run again.
set -euo pipefail

PREFIX="${PREFIX:-$HOME/.local}"
DRY_RUN=0

OPENSTEGO_VERSION=0.8.6
JSTEG_COMMIT=cf206c26c711db215c11cb61bb479113ae8be275
JPHS_COMMIT=33a11e1bad146f5e9c0d3fe6475812a1cedb9b7e
OUTGUESS_TAG=0.4
PNGCHECK_VERSION=3.0.3

# Every executable the analyzers call (see stegray --list-tools).
TOOLS="file strings exiftool binwalk 7z foremost pngcheck identify pdfinfo pdfid
steghide outguess expect jpseek jsteg java openstego zsteg"

usage() {
    cat <<EOF
Usage: $0 [--prefix DIR] [--dry-run]

  --prefix DIR  where tools without a package go (default: $PREFIX)
  --dry-run     print what would be done without doing it
EOF
}

while [ $# -gt 0 ]; do
    case "$1" in
        --prefix) PREFIX="${2:?--prefix needs a directory}"; shift ;;
        --dry-run) DRY_RUN=1 ;;
        -h|--help) usage; exit 0 ;;
        *) usage >&2; exit 2 ;;
    esac
    shift
done

BIN_DIR="$PREFIX/bin"
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ORIGINAL_PATH="$PATH"
export PATH="$BIN_DIR:$PATH"
# Arch puts exiftool here, and only login shells have it on PATH.
PATH="$PATH:/usr/bin/vendor_perl"

info() { printf '\033[1m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[33mwarning:\033[0m %s\n' "$*" >&2; }
die() { printf '\033[31merror:\033[0m %s\n' "$*" >&2; exit 1; }
have() { command -v "$1" >/dev/null 2>&1; }

run() {
    if [ "$DRY_RUN" = 1 ]; then
        printf '    %s\n' "$*"
    else
        "$@"
    fi
}

# ---------------------------------------------------------------------------
# OS and package manager detection
# ---------------------------------------------------------------------------

detect_manager() {
    case "$(uname -s)" in
        Darwin)
            have brew || die "Homebrew is required on macOS: https://brew.sh"
            echo brew
            return
            ;;
        Linux) ;;
        *) die "unsupported OS '$(uname -s)'; use the container image (or WSL on Windows)" ;;
    esac
    for candidate in apt-get dnf pacman zypper; do
        if have "$candidate"; then
            echo "${candidate%-get}"
            return
        fi
    done
    die "no supported package manager found (apt, dnf, pacman, zypper); use the container image"
}

MANAGER="$(detect_manager)"
OS_NAME="$(uname -s)"
if [ -r /etc/os-release ]; then
    OS_NAME="$(. /etc/os-release && echo "${PRETTY_NAME:-$NAME}")"
fi

SUDO=""
if [ "$MANAGER" != brew ] && [ "$(id -u)" -ne 0 ]; then
    have sudo || die "run this as root, or install sudo"
    SUDO=sudo
fi

# Package(s) providing a command under the current manager. Prints nothing when
# the distribution does not package it; those are handled further down.
packages_for() {
    case "$MANAGER:$1" in
        apt:strings | dnf:strings | pacman:strings | zypper:strings) echo binutils ;;
        apt:exiftool) echo libimage-exiftool-perl ;;
        dnf:exiftool) echo perl-Image-ExifTool ;;
        pacman:exiftool) echo perl-image-exiftool ;;
        apt:7z) echo p7zip-full ;;
        dnf:7z | pacman:7z | zypper:7z) echo 7zip ;;
        brew:7z) echo p7zip ;;
        apt:identify) echo graphicsmagick-imagemagick-compat ;;
        dnf:identify | zypper:identify) echo ImageMagick ;;
        pacman:identify | brew:identify) echo imagemagick ;;
        apt:pdfinfo | dnf:pdfinfo) echo poppler-utils ;;
        zypper:pdfinfo) echo poppler-tools ;;
        pacman:pdfinfo | brew:pdfinfo) echo poppler ;;
        apt:java) echo default-jre ;;
        dnf:java) echo java-latest-openjdk ;;
        pacman:java) echo jre-openjdk ;;
        zypper:java) echo java-21-openjdk ;;
        brew:java) echo openjdk ;;
        dnf:gem) echo ruby rubygems ;;
        *:gem) echo ruby ;;
        apt:go) echo golang-go ;;
        dnf:go) echo golang ;;
        apt:cc | apt:c++) echo build-essential ;;
        pacman:cc | pacman:c++) echo base-devel ;;
        dnf:cc | zypper:cc) echo gcc ;;
        dnf:c++ | zypper:c++) echo gcc-c++ ;;
        *:autoreconf) echo autoconf automake ;;
        # macOS ships these (or they come with the Xcode command line tools).
        brew:file | brew:strings | brew:expect | brew:cc | brew:c++ | brew:make | brew:git | brew:curl) ;;
        # Not packaged; installed from source or a release below.
        *:pdfid | *:jpseek | *:jsteg | *:openstego | *:zsteg) ;;
        dnf:outguess | pacman:outguess | pacman:pngcheck) ;;
        # Not packaged and not built here either (on Arch it is in the AUR).
        pacman:steghide | brew:steghide) ;;
        *) echo "$1" ;;
    esac
}

pm_install() {
    case "$MANAGER" in
        apt) run $SUDO env DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends "$@" ;;
        dnf) run $SUDO dnf install -y "$@" ;;
        pacman) run $SUDO pacman -S --needed --noconfirm "$@" ;;
        zypper) run $SUDO zypper --non-interactive install "$@" ;;
        brew) run brew install "$@" ;;
    esac
}

# Install the packages behind the given commands, skipping commands already on
# PATH. One unavailable package must not block the others, so a failed batch is
# retried package by package.
ensure() {
    local packages=() cmd pkg
    for cmd in "$@"; do
        have "$cmd" && continue
        for pkg in $(packages_for "$cmd"); do
            packages+=("$pkg")
        done
    done
    [ ${#packages[@]} -gt 0 ] || return 0
    info "installing packages: ${packages[*]}"
    if [ "$MANAGER" = apt ] && [ -z "${APT_UPDATED:-}" ]; then
        run $SUDO apt-get update
        APT_UPDATED=1
    fi
    pm_install "${packages[@]}" && return 0
    for pkg in "${packages[@]}"; do
        pm_install "$pkg" || warn "could not install package '$pkg'"
    done
}

# ---------------------------------------------------------------------------
# Tools the package managers do not carry
# ---------------------------------------------------------------------------

WORK_DIR=""
cleanup() { [ -z "$WORK_DIR" ] || rm -rf "$WORK_DIR"; }
trap cleanup EXIT

fetch() { # fetch URL DEST
    if have curl; then
        curl -fsSL -o "$2" "$1"
    else
        wget -q -O "$2" "$1"
    fi
}

clone_at() { # clone_at URL REF DEST
    git clone --quiet "$1" "$3"
    git -C "$3" -c advice.detachedHead=false checkout --quiet "$2"
}

install_pdfid() {
    # Didier Stevens' pdfid.py, vendored in docker/ (see the Dockerfile).
    install -m 755 "$REPO_DIR/docker/pdfid.py" "$BIN_DIR/pdfid"
}

install_zsteg() {
    ensure gem
    gem install --user-install --no-document --bindir "$BIN_DIR" zsteg
}

install_jsteg() {
    ensure go
    GOBIN="$BIN_DIR" go install "lukechampine.com/jsteg/cmd/jsteg@$JSTEG_COMMIT"
}

install_openstego() {
    ensure java curl
    local share="$PREFIX/share/openstego"
    fetch "https://github.com/syvaidya/openstego/releases/download/openstego-$OPENSTEGO_VERSION/openstego-$OPENSTEGO_VERSION.zip" \
        "$WORK_DIR/openstego.zip"
    python3 -m zipfile -e "$WORK_DIR/openstego.zip" "$WORK_DIR"
    mkdir -p "$share"
    cp "$WORK_DIR/openstego-$OPENSTEGO_VERSION/lib/openstego.jar" "$share/openstego.jar"
    cat >"$BIN_DIR/openstego" <<EOF
#!/bin/sh
exec java -Xmx1024m -jar "$share/openstego.jar" "\$@"
EOF
    chmod 755 "$BIN_DIR/openstego"
}

install_outguess() {
    ensure cc c++ make git autoreconf  # its configure insists on a C++ compiler
    clone_at https://github.com/resurrecting-open-source-projects/outguess.git "$OUTGUESS_TAG" "$WORK_DIR/outguess"
    (
        cd "$WORK_DIR/outguess"
        # Not --with-generic-jconfig: that leaves the bundled libjpeg 6b with
        # K&R declarations, which C23 compilers reject.
        sed 's/^#undef \(HAVE_[A-Z_]*\)$/#define \1/' src/jpeg-6b-steg/jconfig.cfg >src/jpeg-6b-steg/jconfig.h
        ./autogen.sh >/dev/null
        ./configure --quiet
        make --quiet
    )
    install -m 755 "$WORK_DIR/outguess/src/outguess" "$BIN_DIR/outguess"
}

install_pngcheck() {
    ensure cc curl
    fetch "http://www.libpng.org/pub/png/src/pngcheck-$PNGCHECK_VERSION.tar.gz" "$WORK_DIR/pngcheck.tar.gz"
    tar -xzf "$WORK_DIR/pngcheck.tar.gz" -C "$WORK_DIR"
    cc -O2 -DUSE_ZLIB -o "$BIN_DIR/pngcheck" "$WORK_DIR/pngcheck-$PNGCHECK_VERSION/pngcheck.c" -lz
}

install_jpseek() {
    # Same recipe as the Dockerfile: jphs against its bundled libjpeg 8a.
    ensure cc make git
    local src="$WORK_DIR/jphs"
    clone_at https://github.com/h3xx/jphs.git "$JPHS_COMMIT" "$src"
    cp "$REPO_DIR/docker/config.guess" "$REPO_DIR/docker/config.sub" "$src/jpeg-8a/"
    chmod +x "$src/jpeg-8a/config.guess" "$src/jpeg-8a/config.sub"
    (
        cd "$src/jpeg-8a"
        ./configure --quiet
        make --quiet
    )
    (
        cd "$src"
        sed -i.bak 's/open(seekfilename,O_WRONLY|O_TRUNC|O_CREAT)/open(seekfilename,O_WRONLY|O_TRUNC|O_CREAT, 0644)/' jpseek.c
        sed -i.bak \
            -e 's/^LIBS = .*/JPEG_OBJS = $(filter-out jpeg-8a\/cjpeg.o jpeg-8a\/djpeg.o jpeg-8a\/jpegtran.o jpeg-8a\/rdjpgcom.o jpeg-8a\/wrjpgcom.o,$(wildcard jpeg-8a\/\*.o))/' \
            -e 's/^LDFLAGS = .*/LDFLAGS =/' \
            -e 's/^jphide: \(.*\)$/jphide: \1 $(JPEG_OBJS)/' \
            -e 's/^jpseek: \(.*\)$/jpseek: \1 $(JPEG_OBJS)/' Makefile
        make --quiet all
    )
    install -m 755 "$src/jphide" "$src/jpseek" "$BIN_DIR/"
}

# Run install_<tool> unless the tool is already there; a failure is reported
# and the remaining tools still get their turn.
from_source() {
    have "$1" && return 0
    if [ "$DRY_RUN" = 1 ]; then
        # A dry run installed no packages, so only announce the unpackaged ones.
        [ -n "$(packages_for "$1")" ] || info "would install $1 into $BIN_DIR"
        return 0
    fi
    info "installing $1 into $BIN_DIR"
    # Not `( ... ) || warn`: errexit is ignored inside an || list.
    local status=0
    set +e
    (set -e; "install_$1")
    status=$?
    set -e
    [ "$status" -eq 0 ] || warn "could not install $1"
}

# ---------------------------------------------------------------------------

info "$OS_NAME, package manager: $MANAGER"

# shellcheck disable=SC2086  # word splitting of the tool list is intended
ensure $TOOLS

if [ "$DRY_RUN" = 0 ]; then
    mkdir -p "$BIN_DIR"
    WORK_DIR="$(mktemp -d)"
fi
for tool in pdfid zsteg jsteg openstego pngcheck outguess jpseek; do
    from_source "$tool"
done

[ "$DRY_RUN" = 0 ] || exit 0

missing=""
for tool in $TOOLS; do
    have "$tool" || missing="$missing $tool"
done
case ":$ORIGINAL_PATH:" in
    *":$BIN_DIR:"*) ;;
    *) warn "$BIN_DIR is not on your PATH; add it so stegray finds the tools there" ;;
esac
if [ -n "$missing" ]; then
    warn "still missing:$missing"
    exit 1
fi
info "all tools installed; check with: stegray --list-tools"
