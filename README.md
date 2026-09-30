# Portafolios

Página para seguir tus portafolios: composición, ponderaciones, ganancia y rendimiento contra el S&P 500 (VOO) y Bitcoin, con precios en vivo. Es gratis y no necesita servidor: está publicada con GitHub Pages y los precios los consulta tu navegador.

**Link:** https://juanifernandezhornus.github.io/portfolio-app/

## Cómo se usa

1. **Crear un portafolio:** en la columna de la izquierda, tocá **+ Crear portafolio**.
2. **Cargar una compra o venta:** tocá **+ Agregar operación**. Al escribir el ticker aparece el precio de mercado y, si la fecha es la de hoy, se completa solo. Completá dos de los tres valores (cantidad, precio, monto) y el tercero se calcula.
   - Cripto, acciones y ETFs de EE.UU.: en USD.
   - Bonos, CEDEARs y acciones argentinas: en pesos, con el dólar MEP del día (se completa solo). Los bonos cotizan cada 100 VN.
3. **Seguimiento:** el balance, la tabla de activos y el gráfico se actualizan solos mientras tenés la página abierta.

## Dónde se guardan tus datos

- **Sin configurar nada:** en el navegador donde los cargaste.
- **Para verlos igual en el celu y la compu:** tocá **Sincronizar** (abajo a la izquierda) y conectá un token de GitHub (los pasos están ahí mismo). Los portafolios pasan a guardarse en `data/portfolio.json` de este repo.
  - En un dispositivo sin token, la página muestra los portafolios en **modo lectura**.
  - El repo es público: cualquiera con el link puede ver los portafolios, pero solo vos podés editarlos.
- **Copia de seguridad:** en **Sincronizar** podés descargar o importar un archivo `.json`.

## De dónde salen los precios

| Activo | En vivo (desde el navegador) | Histórico para el gráfico |
|---|---|---|
| Cripto | Binance cada 15 s (si no está, CoinGecko) | Binance (CoinGecko: último año) |
| Bonos, CEDEARs y acciones argentinas | data912 (BYMA) cada 1 min | data912 |
| Acciones de EE.UU. | data912 cada 1 min | Yahoo Finance, vía GitHub Action |
| ETFs de EE.UU. | Solo algunos (VOO sí). El resto usa el cierre diario | Yahoo Finance, vía GitHub Action |
| Dólar MEP | dolarapi cada 1 min | argentinadatos |

Yahoo Finance no deja consultarse desde páginas web, así que el histórico de EE.UU. lo baja una tarea de GitHub (`.github/workflows/history.yml`) todos los días hábiles a las 18:40 (hora argentina) y cada vez que cambiás tus portafolios. Queda en `data/history.json`.

## Rendimiento

- **Ganancia total** = valor actual − aportes netos (compras − ventas).
- **TWR** (time-weighted return): mide el rendimiento sin que las recargas o ventas lo distorsionen. Es el que se compara contra VOO y BTC.
- **Vista "En USD":** muestra cuánto tendrías si hubieras hecho los mismos aportes, los mismos días, en VOO o en BTC.
- Los benchmarks usan precio, sin dividendos.

## Archivos

```
index.html                     la página completa (HTML + CSS + JS, sin build)
data/portfolio.json            tus portafolios (se crea al sincronizar)
data/history.json              cierres diarios de EE.UU. (lo escribe la GitHub Action)
scripts/update_history.py      script que baja el histórico de Yahoo Finance
.github/workflows/history.yml  tarea diaria de GitHub
```
