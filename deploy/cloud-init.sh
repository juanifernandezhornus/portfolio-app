#!/bin/bash
# Script de inicio para Oracle Cloud.
# Se pega en "Show advanced options → Management → Initialization script" al crear la VM (Ubuntu).
# Instala la app sola al prender la máquina. Log: /var/log/portafolios-init.log
INVITE_CODE="CAMBIAR_CODIGO"
REPO="https://github.com/juanifernandezhornus/portfolio-app"

exec > /var/log/portafolios-init.log 2>&1
set -eux
export DEBIAN_FRONTEND=noninteractive
apt-get update -y
apt-get install -y git curl
IP=$(curl -fsS https://api.ipify.org || curl -fsS https://ifconfig.me)
DOMAIN="$(echo "$IP" | tr . -).sslip.io"
rm -rf /opt/portafolios-src
git clone --depth 1 "$REPO" /opt/portafolios-src
cd /opt/portafolios-src
DOMAIN="$DOMAIN" INVITE_CODE="$INVITE_CODE" bash deploy/install.sh
echo "APP LISTA EN https://$DOMAIN"
