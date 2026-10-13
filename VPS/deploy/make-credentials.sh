#!/usr/bin/env bash
# The logins for board.example.com (no sudo needed):
#   var/credentials.txt  the passwords, readable only by you
#   var/caddy-users      their hashes, which deploy/setup-caddy.sh puts in Caddy's config
# There are always two: brother (the page) and agent (the watcher). Every other login uses the page like the brother.
# It never changes a password unless asked. After any change, run `sudo bash deploy/setup-caddy.sh` again.
#   ./deploy/make-credentials.sh                 make the logins that are missing
#   ./deploy/make-credentials.sh --new brother   a new password (for the named logins, or all of them)
#   ./deploy/make-credentials.sh --add guest     one more login for the page
#   ./deploy/make-credentials.sh --remove guest  take a login away (not brother or agent)
set -euo pipefail
cd "$(dirname "$0")/.."
BROTHER=${BROTHER_USER:-brother}
AGENT=${AGENT_USER:-agent}
umask 077
mkdir -p var
chmod 700 var

# the logins there are now, in order, with their passwords
declare -A pass
order=("$BROTHER" "$AGENT")
if [[ -f var/credentials.txt ]]; then
  while read -r user pw _; do
    [[ -n "${user:-}" && "$user" != \#* && -n "${pw:-}" ]] || continue
    pass[$user]=$pw
    [[ " ${order[*]} " == *" $user "* ]] || order+=("$user")
  done < var/credentials.txt
fi

action=${1:-}
[[ $# -gt 0 ]] && shift
case "$action" in
  '') ;;
  --new)
    (( $# )) || set -- "${order[@]}"
    for user in "$@"; do
      [[ " ${order[*]} " == *" $user "* ]] || { echo "there is no login called $user" >&2; exit 1; }
      unset "pass[$user]"
    done ;;
  --add)
    (( $# )) || { echo "which login? ./deploy/make-credentials.sh --add NAME" >&2; exit 1; }
    for user in "$@"; do
      [[ "$user" =~ ^[a-z][a-z0-9_-]{0,31}$ ]] || { echo "$user: use lower-case letters, digits, - or _" >&2; exit 1; }
      [[ " ${order[*]} " == *" $user "* ]] && { echo "$user is already a login (to change its password: --new $user)" >&2; exit 1; }
      order+=("$user")
    done ;;
  --remove)
    (( $# )) || { echo "which login? ./deploy/make-credentials.sh --remove NAME" >&2; exit 1; }
    for user in "$@"; do
      [[ "$user" == "$BROTHER" || "$user" == "$AGENT" ]] && { echo "$user always stays (give it a new password with --new $user)" >&2; exit 1; }
      [[ " ${order[*]} " == *" $user "* ]] || { echo "there is no login called $user" >&2; exit 1; }
      unset "pass[$user]"
      kept=()
      for u in "${order[@]}"; do [[ "$u" == "$user" ]] || kept+=("$u"); done
      order=("${kept[@]}")
    done ;;
  *) sed -n '2,10p' "$0" | sed 's/^# \{0,1\}//' >&2; exit 1 ;;
esac

changed=$([[ "$action" == --remove ]] && echo 1 || echo 0)
for user in "${order[@]}"; do
  if [[ -z "${pass[$user]:-}" ]]; then
    pass[$user]=$(python3 -c 'import secrets; print(secrets.token_urlsafe(18))')
    changed=1
  fi
done
if [[ $changed -eq 0 && -f var/caddy-users ]]; then
  echo "Nothing to change: the logins are ${order[*]} (passwords in var/credentials.txt)."
  exit 0
fi

{
  echo "# board.example.com logins: user  password  (changed $(date +%F))"
  for user in "${order[@]}"; do
    if [[ "$user" == "$AGENT" ]]; then note="the watcher on the agent's machine"; else note="the web page"; fi
    echo "$user ${pass[$user]}   # $note"
  done
} > var/credentials.txt.new
: > var/caddy-users.new
for user in "${order[@]}"; do
  hash=$(printf '%s\n' "${pass[$user]}" | caddy hash-password --algorithm bcrypt)
  [[ "$hash" == \$2* ]] || { echo "caddy hash-password failed for $user" >&2; rm -f var/*.new; exit 1; }
  echo "$user $hash" >> var/caddy-users.new
done
mv var/credentials.txt.new var/credentials.txt
mv var/caddy-users.new var/caddy-users
echo "Logins now: ${order[*]} (passwords in var/credentials.txt). Now run: sudo bash deploy/setup-caddy.sh"
