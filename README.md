# Portafolios

App web para armar portafolios de práctica (plata ficticia) y seguir su rendimiento contra el S&P 500 y Bitcoin, con cotizaciones en vivo. Cada persona crea su cuenta con un código de invitación y ve solo sus portafolios. Los que se marcan como públicos aparecen en el ranking entre amigos.

## Qué hace

- **Cuentas**: registro con código de invitación, usuario y contraseña. Cada uno ve solo lo suyo.
- **Portafolios**: varios por persona, con vista consolidada y comparación entre ellos.
- **Operaciones**: compras y ventas con fecha. El formulario trae el precio de mercado del momento.
- **Cotizaciones en vivo**: cripto cada 20 segundos, el resto cada minuto. La página se refresca sola cada 15 segundos.
- **Rendimiento**: valor, costo promedio, ganancia realizada y no realizada, TWR contra SPY y BTC, y la vista "mismos aportes en el benchmark".
- **Ranking**: los portafolios marcados como públicos, ordenados por TWR.

### De dónde salen los precios (todo gratis, sin claves)

| Activo | Fuente | Histórico |
|---|---|---|
| Cripto | Binance (si falla, Yahoo Finance) | Binance / Yahoo |
| Acciones y ETFs de EE.UU. | Yahoo Finance | Yahoo |
| CEDEARs, acciones y bonos argentinos (ARS) | data912.com (BYMA) | data912 |
| Dólar MEP | dolarapi.com | argentinadatos.com |

Cuando alguien carga una operación con fecha vieja, la app baja sola el histórico de ese activo, de SPY y de BTC desde esa fecha, así el gráfico arranca bien.

> Binance bloquea servidores ubicados en EE.UU. Si tu VM de Oracle está en una región de EE.UU., la cripto sale automáticamente de Yahoo Finance.

---

## Instalación en Oracle Cloud

Recomendado: una **VM nueva** de la capa gratuita (Always Free), separada de la del bot de bonos. Esta app queda abierta a internet y conviene que no comparta máquina con algo que opera con claves de exchange.

### 1. Crear la VM
En la consola de Oracle: **Compute → Instances → Create instance**.
- Imagen: **Ubuntu 22.04 o 24.04**.
- Forma: `VM.Standard.A1.Flex` (ARM, 1 OCPU y 6 GB alcanzan) o `VM.Standard.E2.1.Micro`.
- Descargá la clave SSH y anotá la **IP pública**.

### 2. Abrir los puertos 80 y 443 en Oracle
En la instancia: **Subnet → Security List → Add Ingress Rules**, dos reglas:
- Source CIDR `0.0.0.0/0`, protocolo TCP, puerto destino `80`
- Source CIDR `0.0.0.0/0`, protocolo TCP, puerto destino `443`

(El firewall interno de la VM lo abre el instalador.)

### 3. Elegir el dominio
No hace falta comprar uno. Usá **sslip.io**: tomá tu IP y cambiá los puntos por guiones.
IP `129.146.10.20` → dominio `129-146-10-20.sslip.io`. Funciona solo y con HTTPS.

### 4. Subir el proyecto e instalar
Desde tu compu:
```bash
scp -i tu-clave.key -r portfolio-app ubuntu@129.146.10.20:~/
ssh -i tu-clave.key ubuntu@129.146.10.20
cd portfolio-app
chmod +x deploy/install.sh
sudo DOMAIN=129-146-10-20.sslip.io INVITE_CODE=un-codigo-para-tus-amigos ./deploy/install.sh
```

### 5. Usarla
Abrí `https://129-146-10-20.sslip.io`, tocá **Crear cuenta** y usá el código de invitación. Pasales el link y el código a tus amigos.

---

## Mantenimiento

| Para | Comando (en la VM) |
|---|---|
| Ver si está andando | `systemctl status portafolios` |
| Ver logs (precios, errores) | `journalctl -u portafolios -f` |
| Actualizar el código | subir la carpeta nueva y volver a correr `install.sh` con los mismos datos |
| Cambiar el código de invitación | editar `/opt/portafolios/.env` y `sudo systemctl restart portafolios` |
| Cerrar el registro | dejar `PF_INVITE_CODE=` vacío en el `.env` y reiniciar |

Backups: el instalador deja un backup diario en `/opt/portafolios/backups` (se guardan 14 días). Para bajarte uno: `scp -i tu-clave.key ubuntu@IP:/opt/portafolios/backups/portafolios-FECHA.db .`

## Probar en tu compu

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
PF_DB_PATH=data/dev.db PF_INVITE_CODE=test PF_SECURE_COOKIES=0 uvicorn app.main:app --reload
```
Abrí http://127.0.0.1:8000. Con `PF_MOCK_PRICES=1` los precios son simulados (sirve para probar sin internet).

## Estructura

```
app/main.py        API: cuentas, portafolios, operaciones, cotizaciones, ranking
app/prices.py      cotizaciones en vivo e históricas (Binance, Yahoo, data912, dolarapi)
app/db.py          base SQLite
app/static/index.html   la página (HTML + JS, sin build)
deploy/            instalador, servicio systemd y configuración de Caddy (HTTPS)
```

## Seguridad

- Contraseñas guardadas con scrypt (nunca en texto plano). Sesión en cookie HttpOnly y Secure.
- Registro solo con código de invitación. Límite de intentos de login por IP.
- La app escucha solo en `127.0.0.1`. Caddy es el único expuesto y maneja el HTTPS.
- Los datos son de práctica, pero igual: usá una contraseña que no uses en otros lados.
