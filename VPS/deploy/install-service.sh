#!/usr/bin/env bash
# Run the board as a user service (no sudo; starts on boot because linger is on):  ./deploy/install-service.sh
set -euo pipefail
cd "$(dirname "$0")"
mkdir -p ~/.config/systemd/user
cp gamemaker.service ~/.config/systemd/user/gamemaker.service
systemctl --user daemon-reload
systemctl --user enable gamemaker.service >/dev/null
systemctl --user restart gamemaker.service
sleep 1
systemctl --user --no-pager --lines=3 status gamemaker.service
