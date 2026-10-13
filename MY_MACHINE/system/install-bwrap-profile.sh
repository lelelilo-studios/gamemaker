#!/bin/bash
# Step 2: installs the bubblewrap profile from the Claude Code sandboxing docs
# (https://code.claude.com/docs/en/sandboxing, "Ubuntu 24.04 and later") and loads it.
#
# What the profile is: flags=(unconfined) with one rule, userns. It lets /usr/bin/bwrap create user namespaces,
# which Ubuntu otherwise denies to programs without a profile, so that the sandbox can start. It confines
# nothing: the fence is bubblewrap and Claude Code's settings, not AppArmor. And it is machine-wide: it takes the
# place of Ubuntu's own stricter profile for everything that runs /usr/bin/bwrap, not only for the runs.
#
#   sudo bash ~/.local/share/gamemaker/install-bwrap-profile.sh
#
# Run step 1 first (switching off Ubuntu's own profile of the same name), or the two fight:
#   sudo ln -s /etc/apparmor.d/bwrap-userns-restrict /etc/apparmor.d/disable/
#   sudo apparmor_parser -R /etc/apparmor.d/bwrap-userns-restrict
#
# To undo both steps:
#   sudo rm /etc/apparmor.d/bwrap /etc/apparmor.d/disable/bwrap-userns-restrict
#   sudo systemctl reload apparmor
set -eu
if [ "$(id -u)" != 0 ]; then
  echo "run it with sudo: sudo bash $0" >&2
  exit 1
fi
if [ ! -e /etc/apparmor.d/disable/bwrap-userns-restrict ]; then
  echo "step 1 is not done: Ubuntu's own bwrap profile is still switched on (see the top of this file)" >&2
  exit 1
fi

cat > /etc/apparmor.d/bwrap <<'EOF'
abi <abi/4.0>,
include <tunables/global>

profile bwrap /usr/bin/bwrap flags=(unconfined) {
  userns,
  include if exists <local/bwrap>
}
EOF
apparmor_parser -r /etc/apparmor.d/bwrap
echo "installed and loaded /etc/apparmor.d/bwrap"
