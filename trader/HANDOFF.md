# Traspaso de sesión — Percoco + skills /trader

Actualizado: 2026-11-06 15:57 ET. Leer junto con `CLAUDE.md`.

## Qué existe en el repo

- **`.claude/skills/trader-percoco/`**: la estrategia de Craig Percoco.
  - `percoco.py TICKER [--write] [--json]`: análisis 1h/15m/5m con swings,
    rupturas y retest, FVG con su punto medio (CE), Fibonacci, extensión y
    RSI. Lista setups solo si un FVG de hoy nace de una ruptura vigente.
    Estados posibles: *esperando retroceso*, *EN ZONA*, *CONFIRMADO*,
    *INCOMPLETO* (falta Fib/RSI) y *ENTRADA PASADA*.
  - `paper.py cycle SYM...`: gestiona lo abierto, analiza, abre en papel
    solo lo CONFIRMADO y avisa solo de cambios.
    `paper.py show` muestra el libro y `paper.py update` gestiona lo abierto.
  - Antes de abrir pasa por `/trader-news` → `/trader-timemachine` (el
    objetivo 2R debe llegar primero en al menos 1/3 de las simulaciones
    decididas y el R medio debe ser positivo) → en LONG,
    `/trader-exit` → `/trader-budget` → `/trader-risk`.
  - Salidas: mitad en 2R con stop a breakeven; el resto al swing (si queda
    entre 2R y 4R) o a 4R; cierre a las 15:55 ET.
- **Estado**: `trader/percoco_paper.json` (operaciones simuladas),
  `trader/percoco_watch.json` (últimos estados por setup) y
  `trader/percoco.json` (última señal).

## Configuración vigente (`trader/config.json`)

| Regla | Valor |
|---|---|
| Capital / reserva | 1,000 USD / 10 % (900 USD operables) |
| Máximo por posición | 225 USD (22.5 %), acciones fraccionadas |
| Pérdida máxima | 10 USD por operación, 30 USD por día |
| Operaciones por día | 4 |
| R:R mínimo | 2.0 |
| Ventana de entradas | 09:45–15:30 ET, cierre obligatorio 15:55 |
| Cortos | permitidos en papel (`allow_short: true`) |
| Modo | paper; nunca órdenes reales sin el "sí" del usuario |

## Lo aprendido el 2026-11-05

- El mercado cayó fuerte: SPY pasó de 769.79 a un mínimo de 737.12, con gap
  bajista en la apertura. Casi todas las señales fueron de venta.
- **SPY**: setup de venta con zona 751.67–752.83 (CE 752.25, stop 755.03),
  confluencia con el retest de 753.51 y Fib 50 %. El precio nunca regresó.
  Simulación: el objetivo 4R casi nunca llegaba antes del cierre, y por eso
  se cambió la salida a "mitad en 2R".
- **Escaneo de 132 activos** (112 del escáner + ETFs y acciones grandes):
  ninguno completo. Las empresas de servicios públicos (ED, AWK, SO, CMS,
  DUK, PPL, VZ, TMUS) dejaron setups de venta similares.
- **ED**: venta simulada abierta a las 12:55 a 120.98 (1.859 acciones,
  stop 121.45, 2R en 120.04, objetivo final 119.10). Se abrió ANTES de
  conectar las revisiones; con ellas no habría pasado (simulación 25 % vs 33 %
  necesario; noticias de tasas bajando favorecen a ese sector).
- **Para dinero real (Alpaca)**: sin cortos con fracciones; vender en corto
  requiere cuenta de margen con 2,000 USD o más; con menos de 25,000 USD
  aplica el límite de day trades (verificar la regla vigente). Con 1,000 USD
  en real: solo compras. `/trader-budget` dimensiona en acciones completas
  para órdenes bracket.
- **Expectativa**: el riesgo real por operación queda en ~1 USD por el tope
  de 225 USD. El punto de equilibrio es acertar ~25 % con salidas de ~3R.
  Antes de usar dinero real, juntar 20–30 operaciones en papel y medir el
  porcentaje de aciertos.

## Lo aprendido el 2026-11-06

- Tras la caída del 5-nov (chips -11 %, MU/AMD -17 %, por temor a que la
  victoria demócrata frene los centros de datos), el 6-nov rebotó todo:
  121 de 200 rupturas de rango a las 10:03 y 147 de 200 a las 13:06.
- Rotación del último mes: entra dinero a consumo básico, salud, inmobiliario,
  financieras y bonos largos; sale de chips/IA. Co-Invest: los traders
  rentables van contra la multitud (que compra chips en caída).
- **Bug corregido en `percoco.py`**: Yahoo agrega una vela de 16:00 sin rango;
  creaba FVG falsos al precio de cierre. Ahora solo velas 09:30-16:00 ET
  (rama `fix/percoco-solo-sesion-regular`, falta unir a `master`).
- Vigilancia de MU y AMD 10:12-15:57: AMD SHORT 5m CONFIRMADO a las 13:24
  (entrada 439.17, stop 442.16) pero BLOQUEADO por /trader-news con un
  titular genérico sobre elecciones. MU SHORT 5m en zona 13:43, desapareció.
  Cero operaciones.
- **Pendiente**: el filtro de noticias es demasiado estricto (bloquea
  titulares que no son de la empresa). Revisarlo.
- Quiver Quant no tiene suscripción activa (Congreso, insiders, dark pool).

## Pendiente

1. Revisar el resultado de ED en `paper.py show`.
2. Reevaluar el mercado: correr el escáner otra vez y pasar Percoco por los
   candidatos.
3. Decidir la lista a vigilar y arrancar `/loop 5m paper.py cycle ...`.
4. Ir acumulando operaciones en papel para medir el porcentaje de aciertos.
