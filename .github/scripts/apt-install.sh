#!/usr/bin/env bash
# Installs system packages on a GitHub runner:  apt-install.sh <package>...
#
# The runners take packages from azure.archive.ubuntu.com, then archive.ubuntu.com. When
# ubuntu.com does not resolve (2026-11-11: for hours), apt waits 20 s for every file and the
# job hangs until its time limit. So the mirrors are tried first, and the first one that
# answers is used. The others are full copies run by third parties; apt checks Ubuntu's
# signatures on what it downloads, whichever server it comes from.
set -euo pipefail

MIRRORS=(
  http://azure.archive.ubuntu.com/ubuntu
  http://archive.ubuntu.com/ubuntu
  https://mirrors.edge.kernel.org/ubuntu
  https://mirror.leaseweb.com/ubuntu
  https://mirrors.mit.edu/ubuntu
  https://ftp.halifax.rwth-aachen.de/ubuntu
)
list=/etc/apt/apt-mirrors.txt
release=$(. /etc/os-release && echo "$VERSION_CODENAME")
options=(-o Acquire::Retries=2 -o Acquire::http::Timeout=20 -o Acquire::https::Timeout=20)

answering() {
  local skip=$1 mirror
  for mirror in "${MIRRORS[@]:$skip}"; do
    if curl -fsSL --max-time 8 -o /dev/null "$mirror/dists/$release-updates/InRelease"; then
      echo "$mirror"
      return 0
    fi
    echo "No answer from $mirror" >&2
  done
  return 1
}

install() {
  sudo timeout 600 apt-get "${options[@]}" update &&
    sudo timeout 900 apt-get "${options[@]}" install -y --no-install-recommends "$@"
}

if [ ! -f "$list" ]; then
  # Not a runner image with a mirror list: nothing to choose.
  install "$@"
  exit
fi

# First the mirrors in order, then, if the installation still fails, only the third parties.
for skip in 0 2; do
  if mirror=$(answering "$skip"); then
    echo "Packages from $mirror"
    printf '%s/\tpriority:1\n' "$mirror" | sudo tee "$list" >/dev/null
    if install "$@"; then
      exit 0
    fi
  fi
done
echo "Could not install the packages from any mirror" >&2
exit 1
