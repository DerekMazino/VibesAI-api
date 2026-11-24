# Análisis Técnico Completo del Proyecto: VibesAI-api

> **Fecha de análisis:** Noviembre 2026  
> **Versión analizada:** `1.6.0`  
> **Alcance:** SDK Python (`vibes_api/`), Servidor REST (`server/`), CLI (`vibes_api/cli.py`), Tests (`tests/`), Infraestructura (`Dockerfile`, `docker-compose.yml`) y Documentación.

---

## 1. Resumen Ejecutivo

**VibesAI-api** es un proyecto maduro y funcionalmente muy completo que logra encapsular **78 rutas privadas** de la plataforma `vibes.ai` de Meta en un cliente Python con **más de 130 métodos públicos**, un motor local de edición de líneas de tiempo (`Composition`), un sistema de síntesis de voz con triple respaldo (`tts_fallback`), una herramienta CLI con ~30 subcomandos y un servidor FastAPI listo para desplegarse en Docker.

Sin embargo, el crecimiento acelerado entre las versiones `1.0.0`, `1.2.0` y `1.6.0` ha introducido **deuda técnica estructural, código duplicado y defectos sutiles de concurrencia y cableado interno** que deben corregirse para garantizar estabilidad en producción.

---

## 2. Inventario y Estructura de Componentes

| Componente | Archivo(s) | Líneas aprox. | Responsabilidad Principal | Estado General |
|---|---|---|---|---|
| **Core API Client** | `vibes_api/client.py` | 4,487 | Cliente HTTP (`VibesClient`), gestión de sesión/OIDC, llamadas a las 78 rutas de `vibes.ai`, validadores y parser Midjourney. | 🟡 Funcional pero monolítico; contiene métodos duplicados y helpers desconectados. |
| **Timeline Engine** | `vibes_api/composition.py` | 861 | Manipulación local de la estructura JSON del timeline (tracks, clips de video/audio/imagen, overlays de texto, splits, fades, velocidad). | ✅ Muy sólido, bien diseñado y 100% testeado offline. |
| **TTS Fallback** | `vibes_api/tts_fallback.py` | 462 | Cadena de síntesis de voz (`vibes.ai PlayAI` → `Edge TTS` → `Google Translate TTS`) síncrona y asíncrona, mapeo de voces OpenAI. | ✅ Excelente diseño, resiliente y con soporte sync/async. |
| **Ingredients Builder** | `vibes_api/ingredients.py` | 206 | Constructores tipados (`IngredientRef`, `CreateIngredient`, `build_ingredient_payload`) para personajes, estilos y escenas. | ✅ Limpio y sin deuda técnica. |
| **Domain Models** | `vibes_api/models.py` | 165 | Enumeraciones (`AspectRatio`, `Resolution`, `VideoModel`, `GenerationType`, `IngredientType`, etc.). | ✅ Claro y alineado con los bundles de Next.js. |
| **CLI** | `vibes_api/cli.py` | 852 | Interfaz de línea de comandos basada en `argparse` para todas las operaciones del cliente. | 🟡 Amplia cobertura, pero con argumentos ignorados y falta de comandos de `v1.6.0`. |
| **REST Server** | `server/app.py` | 371 | Servidor FastAPI que expone `VibesClient` vía HTTP REST con autenticación por `X-API-Key` / `Bearer`. | 🔴 Problemas de bloqueo del event loop async, versión desactualizada (`1.3.0`) y baja cobertura de endpoints. |
| **Test Suite** | `tests/test_all.py` | 1,026 | Suite de pruebas unitarias offline con `unittest` (120+ tests). | ✅ Buena cobertura de contratos, aunque faltan tests de HTTP mockeado para flujos de error/401/429. |
| **Despliegue** | `Dockerfile`, `docker-compose.yml` | 24 | Contenedor Python 3.12 slim no-root (`vibes`) con volumen persistente para `VIBES_COOKIE_FILE`. | ✅ Bien configurado; puede optimizarse el caching de capas. |

---

## 3. Hallazgos Críticos y Bugs Detectados (Auditoría de Código)

Durante la inspección línea por línea del código fuente se identificaron los siguientes defectos concretos:

### 🔴 Hallazgo 1: Métodos Duplicados y Sobrescritos en `VibesClient` (`vibes_api/client.py`)
En `vibes_api/client.py`, tres métodos de gestión de sesión están definidos **dos veces** dentro de la misma clase `VibesClient`:
1. `_maybe_refresh_cookie(self, resp)`:
   - Definido en **líneas 263–282** y redefinido idénticamente en **líneas 459–476**.
2. `start_background_refresh(self)` y `stop_background_refresh(self)`:
   - **Primera definición (líneas 426–457):** Crea un hilo demonio llamado `"vibes-session-keeper"` que llama a `self._get("/api/auth/check-token")` y, si captura un `VibesAPIError` con `status == 401`, invoca `self._handle_401()`.
   - **Segunda definición (líneas 478–497):** Sobrescribe silenciosamente la primera definición. Crea un hilo llamado `"vibes-cookie-refresh"` que llama a `self._get("/api/auth/me")` y captura `except Exception: pass` sin ningún manejo explícito ni logging.

### 🔴 Hallazgo 2: Sistema de Rate-Limiting (`HTTP 429`) Desconectado en `VibesClient`
En las líneas 3939–3980 de `vibes_api/client.py` se implementaron:
- `get_rate_limit_status()`
- `_set_rate_limit_cooldown(seconds=60)`
- `_check_rate_limit()`
- `_check_with_rate_limit(resp)`

**El problema:** Ninguno de los métodos de transporte (`_get`, `_post`, `_put`, `_delete`) llama a `_check_rate_limit()` antes de enviar una petición ni a `_check_with_rate_limit(resp)` al recibir la respuesta (todos llaman directamente a `self._check(resp)` en las líneas 535, 548, 558, 562). Como resultado, cuando el servidor devuelve `429 Too Many Requests`, el cooldown **nunca se activa automáticamente** en llamadas reales.

### 🔴 Hallazgo 3: Bloqueo del Event Loop Asíncrono en FastAPI (`server/app.py`)
En `server/app.py`, todos los endpoints (excepto `/api/v1/tts`) están declarados como `async def` (ej. `async def gen_video`, `async def ext_video`, `async def poll_batch`, `async def export_timeline`), pero dentro de su cuerpo invocan métodos **síncronos bloqueantes** de `VibesClient` (basados en `requests.Session` y `time.sleep()`).
- **Impacto:** Cuando un cliente llama a `POST /api/v1/videos/generate` con `poll=True` (por defecto), `time.sleep()` y `requests.get()` bloquean el hilo principal del *event loop* de Uvicorn/FastAPI durante **30 a 300 segundos**. Durante ese tiempo, el servidor entero queda congelado y no puede responder ni siquiera a `/health`.
- **Solución técnica:** En FastAPI, las rutas que llaman a código síncrono bloqueante deben definirse como `def` normales (para que FastAPI las ejecute automáticamente en un *threadpool* vía `anyio.to_thread.run_sync`) o envolver las llamadas con `await asyncio.to_thread(...)`.

### 🟠 Hallazgo 4: Re-login Automático (`401`) Incompleto en Verbos HTTP
En `vibes_api/client.py`:
- `_get()` (líneas 532–543) y `_post()` (líneas 545–554) capturan `VibesAPIError` con `status == 401`, ejecutan `self._handle_401()` (que dispara el flujo OIDC con `AUTH_META_COOKIES`) y reintentan la llamada una vez.
- Sin embargo, `_put()` (línea 556), `_delete()` (línea 560), y las llamadas directas a `self.session.post` / `self.session.patch` / `self.session.get` (en uploads, descargas, moodboard PATCH, clips de música y streams SSE) **no pasan por la lógica de recuperación `401`**. Si la sesión expira justo antes de guardar una composición (`PUT /api/projects/{id}`) o actualizar un moodboard (`PATCH`), la operación falla sin intentar el auto-relogin.

### 🟠 Hallazgo 5: Bug en Filtro `--type` de Ingredientes en la CLI (`vibes_api/cli.py`)
En `vibes_api/cli.py`:
- El subparser `ingredients list` define el argumento `--type` con opciones `["CHARACTER", "STYLE", "SETTING"]` (línea 656).
- Sin embargo, la función `cmd_ingredients_list` (línea 185) ejecuta:
  ```python
  items = client.list_ingredients(owner_filter=args.owner_filter)
  ```
  **ignorando por completo `args.type`** (`ingredient_type=args.type`).

### 🟠 Hallazgo 6: Validación Contradictoria de `aspect_ratio` en la CLI
- Tanto `models.py` como `README.md` enfatizan que el servidor de `vibes.ai` **solo soporta 3 relaciones de aspecto** (`1:1`, `9:16`, `16:9`) y que cualquier otro valor provoca `GENERATION_FAILED`.
- No obstante, `vibes_api/cli.py` (líneas 573, 604, 724) permite en `choices` los valores `["1:1", "9:16", "16:9", "4:5", "3:2", "2:3"]`, induciendo a fallos del lado del servidor.

### 🟡 Hallazgo 7: Falso "Resumable Upload" y Problema N+1 en Consultas
1. **`upload_video_resumable()` (`client.py` líneas 3263–3339):** Aunque acepta `chunk_size=5MB` y documenta que sube el archivo por fragmentos mediante el protocolo `rupload`, el código para archivos `≥ 50 MB` abre el archivo completo y hace un único `self.session.post(self._url("/api/upload-media"), files=files)`, llamando a `on_progress(0, file_size)` antes y `on_progress(file_size, file_size)` al terminar.
2. **`list_media_by_ingredient()` (`client.py` líneas 4212–4245):** Obtiene hasta `limit * 2` elementos de la librería multimedia y luego hace un `self.get_batch(batch_id)` **por cada elemento dentro de un bucle**, sin caché de `batch_id`s ya consultados (varios ítems suelen pertenecer al mismo lote, por lo que consulta el mismo `batch_id` hasta 4 veces seguidas).

### 🟡 Hallazgo 8: Desalineación entre `VibesClient`, `server/app.py` y Documentación
- **Desfase de versión y rutas en `server/app.py`:** El paquete está en `v1.6.0`, pero `server/app.py` declara `version="1.3.0"`, usa modelos Pydantic crípticos de 2 letras (`PC`, `PU`, `VG`, `IG`, etc.), contiene bloques `except: pass` (líneas 32 y 37), convierte el stream SSE de `/api/v1/timeline/chat` en una lista bloqueante en memoria, y no expone funcionalidades clave como:
  - Animación de imágenes (`animate_image`)
  - Regeneración de lotes (`regenerate_batch`)
  - Historial de revisiones (`list_revisions`, `save_revision`)
  - Gestión completa de Moodboards (crear, obtener, actualizar PATCH, eliminar)
  - Operaciones de composición del Timeline (`Composition`)
  - Playables, Colaboradores y Activos de proyecto (`project_assets`)
- **Desfase en `FEATURE_MATRIX.md`:** Aunque incluye la sección 0 de auditoría `v1.6.0` (Nov 2026), las tablas de las secciones 1 a 33 y el resumen estadístico (que afirma que Timeline Composition tiene 0/24 funciones implementadas y Publishing 0/7) quedaron congelados en el estado de `v1.1.0`, contradiciendo lo que ya existe en el código desde `v1.2.0` y `v1.6.0`.

---

## 4. Fortalezas Destacables del Proyecto

1. **Ingeniería Inversa Exhaustiva:** El descubrimiento y mapeo de los 78 endpoints de `vibes.ai` (incluyendo detalles no obvios como `UUID v7` con timestamp en los 48 bits altos, `mg_request_id`, coerción de enums en Python 3.11+ y mutaciones GraphQL para actualizar ingredientes) es sobresaliente.
2. **Arquitectura de `tts_fallback.py`:** Es un ejemplo de diseño limpio, desacoplado, con soporte tanto síncrono como asíncrono, mapeo transparente de voces estilo OpenAI (`alloy`, `echo`, etc.) y pruebas unitarias con mocks sin red.
3. **Motor `Composition` (`vibes_api/composition.py`):** Implementa fielmente toda la lógica del editor de video del cliente web en Python puro sin dependencias externas.
4. **Auto Re-login OIDC Sin Navegador Headless:** La implementación de `_attempt_oidc_relogin` en `client.py` (siguiendo manualmente la cadena de redirecciones `vibes.ai` → `auth.meta.com` → `auth.meta.ai/ecto` → callback con `requests.get(allow_redirects=False)`) resuelve de forma muy ingeniosa la expiración de sesiones de 30 minutos.

