#!/bin/bash
# The browser checks of a Gamemaker run, outside its sandbox: a browser cannot start inside it.
#
#   test-in-browser.sh         the build in the source's dist/web
#   test-in-browser.sh <id>    the site's working copy: its root, and the frozen iteration v/<id>/
#
# A run can start this and cannot change it. It is written so that starting it gives a run nothing more than what
# it prints and the pictures it leaves in the source's shots/:
# - it takes nothing from whoever starts it but that one number: the environment is thrown away;
# - nothing of the working copies is run as a program here. Their built files are served to a headless browser as
#   a web page, by this folder's own copies of the checks (browser-tests/), which serve no link out of the
#   working copy and give the page no internet;
# - the pictures are written as new files in the real shots/ folder, never through a link.
#
# This is the author's script, for his game and his folders. /home/you and USER=you stand for the home folder and
# the user of the machine it runs on: write yours in. They are written in and not read from $HOME on purpose: the
# caller's environment is a run's to set, so nothing here is taken from it.
SELF=/home/you/.local/share/gamemaker/test-in-browser.sh
if [ "${GAMEMAKER_CLEAN:-}" != 1 ]; then
  exec /usr/bin/env -i GAMEMAKER_CLEAN=1 HOME=/home/you USER=you PATH=/usr/bin:/bin:/snap/bin LANG=C.UTF-8 \
    XDG_RUNTIME_DIR="/run/user/$(/usr/bin/id -u)" /bin/bash "$SELF" "$@"
fi
set -u -o pipefail
WORK=/home/you/Projects/gamemaker-world
SITE=/home/you/Projects/lelelilo-world-web
TESTS=/home/you/.local/share/gamemaker/browser-tests
NODE=/usr/bin/node
if [ $# -gt 1 ] || ! [[ "${1:-}" =~ ^[0-9]{0,6}$ ]]; then
  echo "usage: test-in-browser.sh [<iteration's number>]" >&2
  exit 2
fi
ID=${1:-}
cd / || exit 1
mkdir -p /home/you/.cache/gamemaker
PRIV=$(mktemp -d /home/you/.cache/gamemaker/browser.XXXXXXXX) || exit 1
trap 'rm -rf "$PRIV"; "$NODE" "$TESTS/reap.mjs" >/dev/null 2>&1' EXIT
status=0
# (the checks say where they saved a picture: here that is shots/, where it is put afterwards)
check() { "$NODE" "$TESTS/$1" "${@:2}" 2>&1 | sed -u -e "s|$PRIV/intro|shots/intro-at-3s.png|g" -e "s|$PRIV/|shots/|g" || status=1; }

if [ -z "$ID" ]; then
  if [ ! -f "$WORK/dist/web/index.html" ]; then echo "✗ no build in dist/web: run cargo xtask web first"; exit 1; fi
  export GAMEMAKER_WITHIN=$WORK
  echo "== the build in dist/web"
  check web-smoke.mjs "$WORK/dist/web" "$PRIV/web-smoke.png"
  check intro-check.mjs "$WORK/dist/web" "$PRIV/intro"
  "$NODE" "$TESTS/deliver.mjs" "$WORK" "$PRIV/web-smoke.png" web-smoke.png "$PRIV/intro/at-3s.png" intro-at-3s.png || status=1
else
  if [ ! -f "$SITE/v/$ID/index.html" ]; then echo "✗ no v/$ID/ in the site's working copy: run cargo xtask publish $ID first"; exit 1; fi
  export GAMEMAKER_WITHIN=$SITE
  echo "== the site's root"
  check web-smoke.mjs "$SITE" "$PRIV/site-root.png"
  echo "== the frozen iteration v/$ID/"
  SMOKE_AT="v/$ID/" check web-smoke.mjs "$SITE" "$PRIV/site-v$ID.png"
  SMOKE_AT="v/$ID/" check intro-check.mjs "$SITE" "$PRIV/intro"
  "$NODE" "$TESTS/deliver.mjs" "$WORK" "$PRIV/site-root.png" site-root.png "$PRIV/site-v$ID.png" "site-v$ID.png" "$PRIV/intro/at-3s.png" intro-at-3s.png || status=1
fi
"$NODE" "$TESTS/reap.mjs" 2>&1 | tail -1
if [ "$status" = 0 ]; then echo "✓ all the browser checks passed: now read the pictures"; else echo "✗ a browser check failed: see the lines marked ✗ above"; fi
exit "$status"
