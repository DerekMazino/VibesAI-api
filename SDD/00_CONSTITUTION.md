# Constitución del Proyecto: VibesAI-api

> **Versión:** 1.0.0  
> **Estado:** Vigente  
> **Versión actual del paquete:** `1.6.0`  
> **Metodología:** Specification-Driven Development (SDD)

---

## 1. Propósito y Visión

**VibesAI-api** es un ecosistema en Python (SDK, CLI y servidor REST con FastAPI) que proporciona acceso programático completo, resiliente y tipado a la plataforma de creación de video e inteligencia artificial **vibes.ai** (Meta AI Video Creation Studio), cuya API original es privada y fue documentada mediante ingeniería inversa.

Nuestra misión es:
1. **Paridad Funcional Total (1:1)** con las capacidades expuestas por los bundles de Next.js de `vibes.ai`.
2. **Resiliencia Operativa** frente a la naturaleza efímera de las sesiones basadas en cookies, caídas intermitentes de servicios upstream (ej. PlayAI TTS, errores HTTP 500 transitorios en lotes y streams SSE).
3. **Ergonomía para el Desarrollador** a través de tres interfaces sincronizadas: cliente Python (`VibesClient` + `Composition`), interfaz de línea de comandos (`vibes-api`) y servidor REST (`server/app.py`).

---

## 2. Principios Arquitectónicos Innegociables

### Principio I: Paridad Estricta de Protocolo con `vibes.ai`
- **Fidelidad de Payloads:** Todo endpoint de generación (`/api/generate/*`) debe incluir invariablemente identificadores trazables compatibles con el cliente web (`mg_request_id` con formato `www-{uuid_v7}` y `projectId`).
- **Identificadores Ordenados por Tiempo:** Los lotes (`generation-batches`) deben emplear `UUID v7` (RFC 9562) con los 48 bits superiores codificando el timestamp en milisegundos y los prefijos esperados por el backend (`batch-`, `extend-`, `video2video-`, `image2video-`), garantizando compatibilidad con `/api/download/*`.
- **IDs de Composición:** Los clips y pistas del timeline deben respetar el formato `{prefix}-{ms}-{base36(7)}` de la aplicación web.

### Principio II: Resiliencia por Diseño (Zero-Fragility)
- **Gestión Autónoma de Sesión:** El cliente debe soportar refresco pasivo (`Set-Cookie`), mantenimiento activo en segundo plano (`background_refresh`) y re-autenticación automática vía flujo OIDC (`auth.meta.com` → `auth.meta.ai/ecto` → `vibes.ai`) ante respuestas `HTTP 401` en **todos** los verbos HTTP (`GET`, `POST`, `PUT`, `PATCH`, `DELETE`).
- **Degradación Elegante (Graceful Fallback):** Ninguna caída predecible de un proveedor externo debe detener un flujo crítico si existe alternativa. El subsistema TTS debe mantener siempre su cadena de respaldo (`vibes.ai PlayAI` → `Edge TTS` → `Google Translate TTS`) e informar explícitamente qué motor respondió (`engine`).
- **Tolerancia a Fallos Transitorios:** Los endpoints propensos a condiciones de carrera (como la consulta de lotes recién creados o streams SSE) deben implementar reintentos con *exponential backoff* y control activo de *rate limiting* (`HTTP 429`).

### Principio III: No Bloqueo y Concurrencia Segura
- **Aislamiento de I/O en el Servidor REST:** Ningún endpoint `async def` en FastAPI debe ejecutar llamadas HTTP síncronas bloqueantes (`requests.Session`) directamente sobre el *event loop* principal. Toda operación síncrona de `VibesClient` invocada desde un contexto asíncrono debe delegarse a un *threadpool* (`def` estándar en FastAPI o `asyncio.to_thread`) o migrarse a un transporte asíncrono (`httpx.AsyncClient`).
- **Streaming Real:** Los endpoints basados en Server-Sent Events (SSE) como el chat del timeline o la sincronización en tiempo real deben exponerse como `StreamingResponse` reales o generadores, evitando almacenar flujos largos en memoria cuando el cliente espera streaming.

### Principio IV: Modularidad y Responsabilidad Única (SRP)
- Ningún archivo de código fuente debe convertirse en un monolito inmanejable. El límite saludable por módulo es de **≤ 800 líneas**.
- La lógica de dominio local (como `Composition`, constructores de `Ingredients`, parseadores de parámetros Midjourney y validadores offline) debe mantenerse desacoplada del transporte HTTP para poder probarse y utilizarse 100% offline sin requerir credenciales.

### Principio V: Sincronización Tripartita (SDK ↔ CLI ↔ Server)
- Toda funcionalidad de alto valor incorporada al SDK (`vibes_api`) debe evaluar su exposición inmediata en la CLI (`vibes_api/cli.py`) y en el servidor FastAPI (`server/app.py`).
- La versión semántica (`__version__`) debe ser única y compartida entre `pyproject.toml`, `vibes_api/__init__.py` y `server/app.py`.

---

## 3. Estándares de Calidad de Código y Seguridad

1. **Tipado y Modelos Explícitos:**
   - Uso obligatorio de *type hints* (`from __future__ import annotations`) en todas las firmas públicas.
   - En el servidor FastAPI, los modelos Pydantic deben tener nombres descriptivos y semánticos (ej. `VideoGenerateRequest` en lugar de abreviaturas crípticas como `VG` o `EDV`).
   - Prohibido el uso de `except: pass` desnudos. Toda excepción silenciada intencionalmente debe especificar su tipo (`except OSError:`, `except VibesAPIError:`) y, cuando corresponda, registrar un log de diagnóstico (`logging.getLogger("vibes_api")`).
2. **Coerción Segura de Enumeraciones:**
   - Debido al comportamiento de `str(Enum)` en Python 3.11+, todo parámetro que acepte `Union[str, Enum]` debe pasar por el helper `_coerce()` antes de serializarse en JSON o query strings.
3. **Seguridad y Manejo de Credenciales:**
   - `VIBES_META_SESSION`, `AUTH_META_COOKIES` y `VIBES_API_KEY` son secretos críticos. Jamás deben registrarse en texto plano en logs, trazas de error (`VibesAPIError`) ni incluirse en el control de versiones.
   - Los endpoints que expongan estado de sesión (como `/api/v1/current-cookie`) deben estar estrictamente protegidos por autenticación (`VIBES_API_KEY`) o enmascarar el token por defecto.

---

## 4. Estándares de Pruebas (Testing Mandate)

1. **Suite Offline Determinista:**
   - El 100% de las pruebas unitarias en `tests/` debe ejecutarse **sin conexión a internet ni cookies reales** (`unittest.mock`), garantizando ejecución rápida (< 5 segundos) en cualquier entorno CI/CD.
2. **Cobertura de Contratos:**
   - Todo nuevo endpoint descubierto en `vibes.ai` debe registrarse en `TestEndpointCoverage.ALL_ENDPOINTS` y su método público en `TestMethodCoverage.EXPECTED_METHODS`.
   - Toda corrección de bug requiere una prueba de regresión que falle antes del cambio y pase después del cambio.

---

## 5. Gobernanza y Flujo de Trabajo SDD (Specification-Driven Development)

Todo cambio o mejora en el proyecto debe seguir el ciclo SDD:
1. **Especificar (`SDD/`):** Documentar o actualizar el requerimiento, contrato de API o cambio arquitectónico en la carpeta `SDD/`.
2. **Validar Impacto:** Verificar qué capas se ven afectadas (SDK, Composición, TTS, CLI, FastAPI Server, Documentación).
3. **Implementar y Probar:** Realizar los cambios respetando los principios de esta Constitución y ejecutar la suite completa de tests offline.
4. **Sincronizar Documentación:** Actualizar `CHANGELOG.md`, `FEATURE_MATRIX.md` y `README.md` para evitar desajustes entre la documentación y el código real.

