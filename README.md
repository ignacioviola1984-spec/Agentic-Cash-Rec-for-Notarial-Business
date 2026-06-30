# Cash Control por Expediente

Sistema enterprise de **control de caja por expediente** para una escribanía
argentina. Controla fondos recibidos del cliente, gastos recuperables, movimientos
bancarios, conciliación, balances financieros, clasificación de estado, flujo de
revisión humana, auditoría, tableros y reportes — **por expediente**.

Para cada expediente, el sistema responde:

- Cuánto dinero se recibió del cliente.
- Qué movimientos bancarios pertenecen al expediente.
- Qué gastos pagó la escribanía por cuenta del cliente.
- Qué gastos siguen pendientes.
- Si el anticipo del cliente alcanza.
- Si la escribanía está financiando al cliente (y por cuánto).
- Qué saldos quedan por cobrar (o devolver).
- Qué transacciones están sin conciliar.
- Qué expedientes están **OK**, en **Atención**, en **Riesgo** o **Bloqueados**.
- Qué acciones requieren revisión humana.

## Principio de diseño crítico

> **El LLM nunca inventa números financieros.**

Todos los montos provienen de datos cargados o de **cálculos deterministas en
Python** (`Decimal`, sin floats). El LLM solo **clasifica** (etiqueta de gasto),
**normaliza**, **sugiere** conciliaciones/asignaciones y **explica** — nunca
produce un balance, total, estado o monto. La capa LLM es **opcional**: sin
`ANTHROPIC_API_KEY` el sistema usa heurísticas deterministas y funciona completo
offline. Un *muro de grounding* rechaza cualquier texto narrativo del LLM que
contenga un número ausente de los hechos calculados.

## Arquitectura

```
config.py                 Umbrales de negocio y settings (única fuente de verdad)
app.py                    Entrypoint Streamlit (UI enterprise)
cashcontrol/
  domain/                 Núcleo determinista PURO (sin I/O, sin LLM)
    money.py              Dinero exacto (Decimal, centavos, formato AR)
    models.py             Entidades y enums
    engine.py             Motor de balances por expediente
    status.py             Clasificador OK / Atención / Riesgo / Bloqueado
    matching.py           Matcher de conciliación (señales explícitas + score)
  data/                   Persistencia
    db.py                 Esquema SQLite (montos en centavos enteros)
    repository.py         CRUD tipado
    audit.py              Log de auditoría encadenado por hash (tamper-evident)
  services/               Orquestación (núcleo + persistencia + auditoría)
    ingestion.py          Importación tolerante CSV/Excel
    matching_service.py   Sugerir / confirmar / rechazar / asignar (HITL)
    status_service.py     Recalcular estado + generar revisiones
    review_service.py     Resolver / descartar revisiones
    reports.py            Reportes y exportación CSV/Excel
    queries.py            Read-model para UI y reportes
    seed.py               Datos de ejemplo (4 estados, todos los flujos)
  llm/                    Capa LLM con guardas + fallback determinista
  ui/                     Páginas Streamlit
tests/                    Suite de tests (núcleo + flujo end-to-end)
sample_data/              Planillas de ejemplo importables
```

El **límite de confianza** es `cashcontrol/domain`: todo lo que produce un número
vive ahí y es 100% determinista y testeado.

## Instalación

Requisitos: Python 3.11+.

```bash
pip install -r requirements.txt
```

(Opcional) Para habilitar la capa LLM, copiá `.env.example` a `.env` y cargá
`ANTHROPIC_API_KEY`. Sin clave, el sistema corre en modo determinista.

## Ejecución

```bash
streamlit run app.py
```

En la barra lateral, **Cargar datos de ejemplo** para ver los cuatro estados y
todos los flujos. O importá tus planillas desde **Carga de datos**.

## Páginas

| Página | Qué hace |
|---|---|
| **Tablero** | KPIs de cartera, distribución por estado, lista priorizada. |
| **Expediente** | Respuestas determinadas a todas las preguntas clave; anticipos, gastos, movimientos, conciliación, revisiones, exportación. |
| **Carga de datos** | Importa expedientes / anticipos / gastos / movimientos (CSV o Excel), con plantillas descargables. |
| **Conciliación** | Asigna movimientos sin expediente y confirma/rechaza sugerencias de match. |
| **Revisión** | Cola global de acciones que requieren decisión humana, por severidad. |
| **Reportes** | Tabla de cartera y exportación CSV/Excel. |
| **Auditoría** | Verifica la cadena de hash y muestra el log inmutable de eventos. |

## Modelo financiero (determinista)

Por expediente, con `recibido` = anticipos del cliente y los gastos **a cargo de
la escribanía** (`pagado_por = escribania`):

- `costo_recuperable` = Σ gastos a cargo de la escribanía (pagados + pendientes).
- `gastos_pagados` = Σ gastos efectivamente desembolsados por la escribanía.
- `posición_caja` = `recibido − gastos_pagados`.
  - `< 0` ⇒ **la escribanía financia** al cliente (`monto_financiado`).
  - `> 0` ⇒ fondos del cliente en poder de la escribanía.
- `balance_neto` = `recibido − costo_recuperable`.
  - `< 0` ⇒ `saldo_a_cobrar`.  `> 0` ⇒ `excedente_a_devolver`.
- `cobertura` = `recibido / costo_recuperable`.
- Los gastos `pagado_por = cliente` se informan pero **no** consumen caja ni se
  consideran recuperables (el cliente ya los abonó directamente).

### Clasificación de estado (orden de severidad)

1. **Bloqueado** — hay revisiones *bloqueantes* abiertas (p. ej. la escribanía
   pagó gastos sin haber recibido ningún anticipo, o datos negativos inválidos).
2. **Riesgo** — financiamiento ≥ umbral, o saldo a cobrar ≥ umbral, o cobertura
   por debajo del mínimo con gastos pendientes.
3. **Atención** — financiamiento menor, anticipo insuficiente, fondos no cubren
   pendientes, o movimientos sin conciliar.
4. **OK** — anticipo suficiente, sin financiamiento ni pendientes descubiertos.

Los umbrales se editan en `config.py` (`Thresholds`).

## Conciliación (HITL)

El matcher determinista sugiere vínculos movimiento ↔ anticipo/gasto usando
señales explícitas: **monto** (ancla), **proximidad de fecha** y **coincidencia
de texto**, con un score 0..1 y una justificación. Una sugerencia **no** es una
conciliación: un humano la confirma. Para movimientos sin expediente, el sistema
**sugiere** un expediente (por código o nombre del cliente), pero la asignación es
siempre una acción humana.

## Importación de datos

Los encabezados se reconocen de forma flexible (mayúsculas/acentos/sinónimos) y
los montos aceptan formato argentino (`1.234.567,89`) o anglosajón. Ver
`sample_data/` para ejemplos importables:

- `expedientes.csv` — `codigo, caratula, cliente, escribano, tipo_acto, fecha_apertura`
- `anticipos.csv` — `expediente, fecha, monto, metodo, referencia`
- `gastos.csv` — `expediente, fecha, monto, categoria, concepto, estado, pagado_por, proveedor`
  (si `categoria` está vacía, la clasifica la capa LLM/heurística)
- `movimientos.csv` — `fecha, monto|credito|debito, descripcion, contraparte, referencia, expediente`

## Auditoría

Cada cambio de estado registra un evento en un log **encadenado por hash**
(`hash = sha256(prev_hash + payload)`). Alterar o borrar un registro histórico
rompe todos los hashes siguientes; la página **Auditoría** lo detecta con
`verify_chain`.

## Tests

```bash
python -m pytest -q
```

Cubre dinero exacto, motor de balances, clasificador de estado, matcher,
integridad de la cadena de auditoría e ingestión/flujo end-to-end (seed →
clasificación → conciliación → reporte).

## Agentes de análisis (IA)

Sobre el núcleo determinista hay una capa de **agentes que interpretan los
resultados y producen análisis + recomendaciones accionables**, a dos niveles:

- **Por expediente** (pestaña *🤖 Análisis IA*): diagnóstico, riesgos y pasos
  concretos (reponer fondos, conciliar, cobrar saldos, regularizar, devolver
  excedentes).
- **De cartera** (tablero): priorización — qué expedientes atender primero.

Garantías de producción:

- **El agente nunca inventa un número.** Un *muro de grounding* verifica que toda
  cifra del texto generado exista en los hechos deterministas; si aparece una
  cifra nueva, la salida se rechaza.
- **Degradación elegante.** Sin API, con la API caída, respuesta malformada o
  grounding fallido, el agente cae a **recomendaciones deterministas por reglas**.
  La función nunca rompe la app desplegada.
- **Control de costo y auditoría.** Cada análisis se cachea por hash de los hechos
  (no se repite la llamada si los números no cambiaron), se persiste y se registra
  en el log de auditoría (solo metadatos: scope, origen, modelo, grounded).

### Activación

El agente usa la API de Anthropic. Configurá la clave:

- **Local:** `ANTHROPIC_API_KEY` en `.env` (ver `.env.example`).
- **Streamlit Community Cloud:** *Manage app → Settings → Secrets* y agregá
  ```toml
  ANTHROPIC_API_KEY = "sk-ant-..."
  CASHCONTROL_LLM_MODEL = "claude-opus-4-8"   # opcional
  ```

Sin clave, la capa de agentes funciona en **modo determinista** (reglas), por lo
que el sistema sigue operativo.

### Backend de datos

SQLite local por defecto. Configurable a **libSQL/Turso** definiendo
`TURSO_DATABASE_URL` y `TURSO_AUTH_TOKEN` (mismo esquema y repositorios). Para un
arranque sin datos de ejemplo, `CASHCONTROL_AUTOSEED=0`.

## Alcance

El sistema implementa **completamente** el control de caja por expediente. No
incluye automatización de documentos legales, asesoramiento legal, compliance UIF,
motor impositivo, contabilidad completa, ERP, pagos bancarios automáticos ni
comunicación autónoma con clientes.
