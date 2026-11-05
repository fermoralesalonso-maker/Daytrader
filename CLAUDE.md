# Daytrader — FEMO servicios industriales

Responder en español, simple y corto (el usuario lee en el teléfono).
Al iniciar una sesión, leer `trader/HANDOFF.md` (estado y lo aprendido).

## Proceso de trading (recuérdaselo al usuario en cada aviso de operación)

1. **Escáner** (/trader-scanner) y **noticias** (/trader-news): candidatos del día.
2. **Percoco** (/trader-percoco): ruptura + retest + FVG + confluencia (Fib/RSI)
   + vela de reacción. Sin todo eso: NO TRADE.
3. **Revisiones antes de entrar** (automáticas en `paper.py`):
   noticias → simulación de 10,000 escenarios (/trader-timemachine) →
   en compras, /trader-exit → /trader-budget → /trader-risk.
4. **Entrada en papel** automática solo si todo pasa.
5. **Gestión**: mitad en 2R + stop a breakeven; resto al swing o 4R; todo
   cerrado a las 15:55 ET.
6. **Dinero real**: solo con /trader-broker y el "sí" explícito del usuario.
   Nunca colocar órdenes reales sin ese sí.

Límites vigentes en `trader/config.json` (capital 1,000 USD, 225 USD por
posición, pérdida máx. 10 USD por operación y 30 USD al día, 4 operaciones).
