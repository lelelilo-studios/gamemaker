#!/usr/bin/env bash
# Put board.example.com in Caddy (run with sudo):  sudo bash deploy/setup-caddy.sh
# - takes deploy/Caddyfile.gamemaker, with the logins from var/caddy-users (make them first: ./deploy/make-credentials.sh)
# - puts it in /etc/caddy/Caddyfile between "# >>> board.example.com" and "# <<<" markers, in place of the
#   previous copy if there is one. Every other site in the file is left as it is.
# - checks the new config before it goes in (the old one is backed up next to it), then reloads Caddy
# Safe to run again, e.g. after changing the allowed IPs or a password.
# board.example.com stands for your board's name. The site's real address is the first line of the block in
# Caddyfile.gamemaker; in this script the name only labels the markers and the last line printed. Change it in
# all of them together (the two awk patterns have it with \. for the dots) or leave it as it is.
set -euo pipefail

CADDYFILE=/etc/caddy/Caddyfile
HERE="$(cd "$(dirname "$0")" && pwd)"
SITE="$HERE/Caddyfile.gamemaker"
USERS="$HERE/../var/caddy-users"

[[ $EUID -eq 0 ]] || { echo "run with sudo: sudo bash deploy/setup-caddy.sh"; exit 1; }
[[ -s "$USERS" ]] || { echo "no logins yet: run ./deploy/make-credentials.sh first (without sudo)"; exit 1; }

work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT

# the site block, with one "user hash" line per login where {{USERS}} is
awk 'FNR == NR { if (NF == 2) users = users "\t\t\t" $1 " " $2 "\n"; next }
     /\{\{USERS\}\}/ { printf "%s", users; next }
     { print }' "$USERS" "$SITE" > "$work/site"

# the current file without our previous block, then ours at the end
awk '/^# >>> board\.example\.com/ { skip = 1; next }
     skip { if (/^# <<< board\.example\.com/) skip = 0; next }
     { print }' "$CADDYFILE" | sed -e :a -e '/^\n*$/{$d;N;ba' -e '}' > "$work/Caddyfile"
{
  echo
  echo "# >>> board.example.com (managed by gamemaker/deploy/setup-caddy.sh: edit deploy/Caddyfile.gamemaker instead)"
  cat "$work/site"
  echo "# <<< board.example.com"
} >> "$work/Caddyfile"

caddy fmt --overwrite "$work/Caddyfile" >/dev/null 2>&1 || true
caddy validate --config "$work/Caddyfile" --adapter caddyfile

cp "$CADDYFILE" "$CADDYFILE.bak.$(date +%Y%m%d%H%M%S)"
cat "$work/Caddyfile" > "$CADDYFILE"
systemctl reload caddy
ips=$(sed -n 's/.*not remote_ip //p' "$SITE")
logins=$(awk 'NF == 2 { printf "%s%s", sep, $1; sep = ", " }' "$USERS")
echo "Caddy reloaded: https://board.example.com"
echo "  from: $ips"
echo "  logins: $logins (passwords in var/credentials.txt)"
