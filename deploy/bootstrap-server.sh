#!/usr/bin/env bash
# Run once on the Ubuntu server as root. Review APP_REPOSITORY before running.
set -Eeuo pipefail

readonly APP_REPOSITORY='https://github.com/wisnufebriramadhan/go-tracker.git'
readonly APP_ROOT=/opt/go-tracker
readonly DEPLOY_USER=deployer

apt-get update
apt-get install --yes git python3 python3-venv python3-pip
id -u "$DEPLOY_USER" >/dev/null 2>&1 || useradd --create-home --shell /bin/bash "$DEPLOY_USER"
install -d -o "$DEPLOY_USER" -g "$DEPLOY_USER" "$APP_ROOT" "$APP_ROOT/data"
chown www-data:www-data "$APP_ROOT/data"
install -d -m 750 -o www-data -g www-data "$APP_ROOT/data/uploads"

if [[ ! -d "$APP_ROOT/app/.git" ]]; then
  sudo -u "$DEPLOY_USER" git clone "$APP_REPOSITORY" "$APP_ROOT/app"
fi

if [[ ! -e "$APP_ROOT/.env" ]]; then
  install -m 640 -o root -g www-data "$APP_ROOT/app/.env.example" "$APP_ROOT/.env"
fi
install -m 755 -o root -g root "$APP_ROOT/app/deploy/deploy-go-tracker" /usr/local/sbin/deploy-go-tracker
install -m 644 -o root -g root "$APP_ROOT/app/deploy/go-tracker.service" /etc/systemd/system/go-tracker.service
install -m 440 -o root -g root /dev/stdin /etc/sudoers.d/go-tracker-deploy <<'SUDOERS'
deployer ALL=(root) NOPASSWD: /usr/local/sbin/deploy-go-tracker
SUDOERS

systemctl daemon-reload
/usr/local/sbin/deploy-go-tracker
