# Prompt para Claude Code

Abrí Claude Code dentro de la carpeta `portfolio-app` (en tu compu) y pegá esto, completando los datos entre corchetes:

---

Quiero desplegar esta app (FastAPI + SQLite, instrucciones en README.md) en mi VM de Oracle Cloud.

Datos:
- IP pública de la VM: [IP]
- Usuario SSH: ubuntu
- Ruta de la clave SSH en mi compu: [ruta/a/la-clave.key]
- Código de invitación para mis amigos: [código]

Pasos:
1. Leé README.md y deploy/install.sh.
2. Verificá que puedo conectarme por SSH a la VM y que es Ubuntu. Si no es Ubuntu, avisame antes de seguir.
3. Copiá la carpeta del proyecto a la VM (sin la carpeta data/ ni .venv/) y corré `sudo DOMAIN=<ip-con-guiones>.sslip.io INVITE_CODE=<código> ./deploy/install.sh`.
4. Si el instalador falla, mirá `journalctl -u portafolios -n 80` y `journalctl -u caddy -n 80`, corregí y volvé a correrlo.
5. Verificá desde la VM que responde `curl -s http://127.0.0.1:8000/` y desde mi compu que abre `https://<ip-con-guiones>.sslip.io`. Si HTTPS no responde, recordame revisar que la Security List de la subnet tenga abiertos los puertos 80 y 443 (eso se hace en la consola web de Oracle, no desde la VM).
6. Mirá los logs durante un minuto y confirmame que las cotizaciones de SPY y BTC se están actualizando sin errores (si Binance devuelve 451, es normal en regiones de EE.UU.: la app usa Yahoo Finance).
7. Al final decime el link para abrir la app y cómo actualizarla la próxima vez.
