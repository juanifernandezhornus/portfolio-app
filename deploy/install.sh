#!/usr/bin/env bash
# Instala o actualiza la app en una VM Ubuntu (Oracle Cloud).
# Uso:   sudo DOMAIN=129-146-10-20.sslip.io INVITE_CODE=tu-codigo ./deploy/install.sh
# Volver a correrlo actualiza el código y conserva la base de datos y el .env.
set -euo pipefail

: "${DOMAIN:?Falta DOMAIN (ej. 129-146-10-20.sslip.io)}"
: "${INVITE_CODE:?Falta INVITE_CODE (el código para tus amigos)}"
APP_DIR=/opt/portafolios
SRC="$(cd "$(dirname "$0")/.." && pwd)"

echo "==> Paquetes del sistema"
apt-get update -y
apt-get install -y python3-venv python3-pip curl rsync gnupg debian-keyring debian-archive-keyring apt-transport-https sqlite3

if ! command -v caddy >/dev/null 2>&1; then
  echo "==> Instalando Caddy (HTTPS automático)"
  curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | gpg --dearmor --yes -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
  curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' > /etc/apt/sources.list.d/caddy-stable.list
  apt-get update -y && apt-get install -y caddy
fi

echo "==> Código de la app en $APP_DIR"
id -u portafolios >/dev/null 2>&1 || useradd --system --home "$APP_DIR" --shell /usr/sbin/nologin portafolios
mkdir -p "$APP_DIR/data"
rsync -a --delete --exclude data --exclude .venv --exclude .env --exclude .git "$SRC/" "$APP_DIR/"
[ -d "$APP_DIR/.venv" ] || python3 -m venv "$APP_DIR/.venv"
"$APP_DIR/.venv/bin/pip" install -q --upgrade pip
"$APP_DIR/.venv/bin/pip" install -q -r "$APP_DIR/requirements.txt"

if [ ! -f "$APP_DIR/.env" ]; then
  cat > "$APP_DIR/.env" <<EOF
PF_DB_PATH=$APP_DIR/data/portafolios.db
PF_INVITE_CODE=$INVITE_CODE
PF_SECURE_COOKIES=1
PF_FAST_INTERVAL=20
PF_SLOW_INTERVAL=60
EOF
fi
chown -R portafolios:portafolios "$APP_DIR"
chmod 600 "$APP_DIR/.env"

echo "==> Servicios"
cp "$APP_DIR/deploy/portafolios.service" /etc/systemd/system/portafolios.service
sed "s/{DOMAIN}/$DOMAIN/" "$APP_DIR/deploy/Caddyfile" > /etc/caddy/Caddyfile

echo "==> Firewall interno de la VM (las imágenes de Oracle bloquean todo salvo SSH)"
for port in 80 443; do
  if ! iptables -C INPUT -p tcp -m state --state NEW --dport "$port" -j ACCEPT 2>/dev/null; then
    pos=$(iptables -L INPUT --line-numbers | awk '/REJECT/ {print $1; exit}')
    if [ -n "$pos" ]; then iptables -I INPUT "$pos" -p tcp -m state --state NEW --dport "$port" -j ACCEPT
    else iptables -A INPUT -p tcp -m state --state NEW --dport "$port" -j ACCEPT; fi
  fi
done
if command -v netfilter-persistent >/dev/null 2>&1; then netfilter-persistent save; fi

systemctl daemon-reload
systemctl enable portafolios >/dev/null
systemctl restart portafolios
systemctl enable caddy >/dev/null
systemctl restart caddy

echo "==> Backup diario de la base (se guardan 14 días)"
cat > /etc/cron.daily/portafolios-backup <<EOF
#!/bin/sh
mkdir -p $APP_DIR/backups
sqlite3 $APP_DIR/data/portafolios.db ".backup '$APP_DIR/backups/portafolios-\$(date +%F).db'"
find $APP_DIR/backups -name '*.db' -mtime +14 -delete
EOF
chmod +x /etc/cron.daily/portafolios-backup

sleep 2
if curl -fsS http://127.0.0.1:8000/ >/dev/null; then
  echo ""
  echo "Listo. Abrí https://$DOMAIN (el certificado HTTPS puede tardar 1 minuto la primera vez)."
else
  echo "La app no respondió. Mirá los logs con: journalctl -u portafolios -n 50"
  exit 1
fi
