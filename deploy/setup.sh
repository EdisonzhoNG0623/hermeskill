#!/usr/bin/env bash
# Stasis single-VM deploy bootstrap (Ubuntu 24.04+ assumed).
#
# Idempotent: re-running upgrades the app from /opt/stasis. Does not touch
# customer data in Postgres.
#
# Usage:
#   sudo ./deploy/setup.sh
#
# Env vars required for first run:
#   STASIS_DB_PASSWORD  — password to set for the `stasis` Postgres role
#
# Optional:
#   STASIS_SOURCE_DIR   — path to the checked-out repo (default: pwd)

set -euo pipefail

if [[ "${EUID}" -ne 0 ]]; then
  echo "must run as root" >&2
  exit 1
fi

if [[ -z "${STASIS_DB_PASSWORD:-}" ]]; then
  echo "STASIS_DB_PASSWORD must be set" >&2
  exit 1
fi

SRC="${STASIS_SOURCE_DIR:-$(pwd)}"
APP_DIR=/opt/stasis
SERVICE_USER=stasis

echo ">>> installing system packages"
apt-get update -y
apt-get install -y postgresql-18 postgresql-contrib-18 python3.12 python3.12-venv \
    python3-pip nginx ufw curl

echo ">>> ensuring postgres role + db exist"
sudo -u postgres psql <<SQL
DO \$\$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'stasis') THEN
    CREATE ROLE stasis LOGIN PASSWORD '${STASIS_DB_PASSWORD}';
  ELSE
    ALTER ROLE stasis WITH PASSWORD '${STASIS_DB_PASSWORD}';
  END IF;
END
\$\$;
SQL
sudo -u postgres psql -tAc \
  "SELECT 1 FROM pg_database WHERE datname='stasis'" | grep -q 1 || \
  sudo -u postgres createdb -O stasis stasis

echo ">>> creating service user + app dir"
id -u "${SERVICE_USER}" >/dev/null 2>&1 || useradd --system --home "${APP_DIR}" --shell /usr/sbin/nologin "${SERVICE_USER}"
install -d -o "${SERVICE_USER}" -g "${SERVICE_USER}" "${APP_DIR}"
rsync -a --delete --exclude='.venv' --exclude='__pycache__' "${SRC}/" "${APP_DIR}/"
chown -R "${SERVICE_USER}:${SERVICE_USER}" "${APP_DIR}"

echo ">>> creating venv + installing"
sudo -u "${SERVICE_USER}" python3.12 -m venv "${APP_DIR}/.venv"
sudo -u "${SERVICE_USER}" "${APP_DIR}/.venv/bin/pip" install --upgrade pip
sudo -u "${SERVICE_USER}" "${APP_DIR}/.venv/bin/pip" install \
    -e "${APP_DIR}/packages/stasis-sdk" \
    -e "${APP_DIR}/packages/stasis-control-plane"

echo ">>> writing systemd unit"
install -m 0644 "${SRC}/deploy/stasis-control-plane.service" \
    /etc/systemd/system/stasis-control-plane.service

cat >/etc/systemd/system/stasis-control-plane.service.d/override.conf <<EOF
[Service]
Environment=STASIS_DB_URL=postgresql+psycopg://stasis:${STASIS_DB_PASSWORD}@localhost:5432/stasis
EOF
mkdir -p /etc/systemd/system/stasis-control-plane.service.d

echo ">>> running migrations"
sudo -u "${SERVICE_USER}" \
  STASIS_DB_URL="postgresql+psycopg://stasis:${STASIS_DB_PASSWORD}@localhost:5432/stasis" \
  "${APP_DIR}/.venv/bin/alembic" -c "${APP_DIR}/packages/stasis-control-plane/alembic.ini" upgrade head

echo ">>> enabling firewall + service"
ufw allow OpenSSH || true
ufw allow 'Nginx Full' || true
ufw --force enable

systemctl daemon-reload
systemctl enable stasis-control-plane.service
systemctl restart stasis-control-plane.service

echo ">>> done. health check:"
sleep 2
curl -fsS http://127.0.0.1:8000/healthz | tee /dev/stderr; echo
