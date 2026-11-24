# Especificación de Mejoras y Hoja de Ruta (Improvement Roadmap)

> **Documento:** `04_IMPROVEMENT_ROADMAP_SPEC.md`  
> **Estado:** P0, P1 y P2-01 Implementados (Verificados con suite offline)  
> **Objetivo:** Corregir los defectos detectados en el análisis (`01_SYSTEM_ANALYSIS.md`) y elevar el proyecto a estándares de producción (`v1.7.0` / `v2.0.0`).

---

## 0. Estado de Ejecución

| Spec ID | Descripción | Estado |
|---|---|---|
| `SPEC-P0-01` | Eliminación de métodos duplicados en `VibesClient` | ✅ Completado |
| `SPEC-P0-02` | Rate limiting `429` + auto-relogin `401` unificado en `_request` (`GET/POST/PUT/PATCH/DELETE`) | ✅ Completado |
| `SPEC-P0-03` | Desbloqueo del event loop en FastAPI (`def` en rutas síncronas), `finally` en `download_media`, versión sincronizada | ✅ Completado |
| `SPEC-P0-04` | Fix de `--type` en `cli.py` + restricción de `--aspect-ratio` a `1:1`, `9:16`, `16:9` | ✅ Completado |
| `SPEC-P0-05` | *(Hallazgo live)* Fix de `generate_video(start_frame=...)` (`type="image"`) y `export_timeline()` con errores tipo `str` | ✅ Completado |
| `SPEC-P1-01` | Paridad `v1.6.0` en FastAPI + streaming SSE real (`/api/v1/timeline/chat/stream`) | ✅ Completado |
| `SPEC-P1-02` | Paridad `v1.6.0` en CLI (`revisions`, `last-export`, `media get/favorite/projects`, `ingredients projects/bulk-delete`) | ✅ Completado |
| `SPEC-P2-01` | Caché de lotes (`batch_cache`) en `list_media_by_ingredient()` | ✅ Completado |
| `SPEC-P2-02` | Modularización progresiva de `client.py` en `_mixins/` (planificado para `v2.0.0`) | ⏳ Pendiente (`v2.0`) |
| `SPEC-P2-03` | Nota de sincronización en `FEATURE_MATRIX.md` | ✅ Completado |

---

## 1. Matriz de Priorización

| Prioridad | Categoría | Enfoque | Esfuerzo Estimado |
|---|---|---|---|
| **P0 (Crítico)** | Corrección de Bugs y Concurrencia | Eliminar código duplicado, conectar rate-limiting, corregir bloqueo del event loop en FastAPI y filtro CLI ignorado. | Bajo – Medio |
| **P1 (Alto)** | Paridad de Interfaces (Server & CLI) | Exponer endpoints faltantes de `v1.6.0` en FastAPI y CLI, streaming SSE real en FastAPI y auto-relogin en todos los verbos HTTP. | Medio |
| **P2 (Medio)** | Refactorización Arquitectónica y Rendimiento | Modularizar `vibes_api/client.py` en mixins, optimizar caché de lotes en `list_media_by_ingredient` y sincronizar `FEATURE_MATRIX.md`. | Medio – Alto |
| **P3 (Evolutivo)** | Nuevas Capacidades (`v2.0.0`) | Cliente HTTP asíncrono nativo (`AsyncVibesClient` con `httpx`), auto-redimensionado de imágenes con Pillow y tipado estricto de respuestas (`TypedDict` / Pydantic). | Alto |

---

## 2. Especificaciones Detalladas de Mejoras (P0 — Correcciones Inmediatas)

### SPEC-P0-01: Eliminación de Métodos Duplicados en `VibesClient`
- **Archivo:** `vibes_api/client.py` (líneas 263–282 vs. 459–476, y 426–457 vs. 478–497).
- **Problema:** `_maybe_refresh_cookie`, `start_background_refresh` y `stop_background_refresh` están definidos dos veces. La segunda definición de `start_background_refresh` sobrescribe la primera y pierde el manejo explícito de `401` (`self._handle_401()`).
- **Especificación de la solución:**
  1. Eliminar las definiciones duplicadas de las líneas 459–498.
  2. En la definición única de `start_background_refresh()`, realizar el ping periódico a `/api/auth/me` (o `/api/auth/check-token`) asegurando que ante `VibesAPIError(status=401)` se invoque `self._handle_401()`.
- **Criterio de Aceptación:**
  - No existen métodos con el mismo nombre definidos más de una vez en `VibesClient`.
  - Un test unitario verifica que cuando el hilo o ciclo de refresh recibe `401`, se dispara `_handle_401()`.

---

### SPEC-P0-02: Activación del Control de Rate Limiting (`HTTP 429`) y Auto-Relogin Unificado
- **Archivo:** `vibes_api/client.py` (líneas 509–563 y 3939–3980).
- **Problema:** `_check_rate_limit()` y `_check_with_rate_limit()` existen pero no son llamados por `_get`, `_post`, `_put`, `_delete`. Además, `_put` y `_delete` no reintentan con `_handle_401()` ante un error `401`.
- **Especificación de la solución:**
  1. Integrar en `_check(resp)` la detección de `resp.status_code == 429`: extraer el header `Retry-After` (si es numérico, o usar `60` segundos por defecto) y llamar a `self._set_rate_limit_cooldown(seconds)`.
  2. Crear un método central `_request(method, path, **kw)` (o actualizar `_get`, `_post`, `_put`, `_delete`) para que:
     - Verifique `self._check_rate_limit()` antes de disparar la llamada HTTP.
     - Si recibe `VibesAPIError` con `status == 401` y `not self._session_expired`, ejecute `if self._handle_401():` y reintente la petición **una vez** para cualquier verbo (`GET`, `POST`, `PUT`, `PATCH`, `DELETE`).
- **Criterio de Aceptación:**
  - Un test unitario con mock de `requests.Session` simula una respuesta `429` y comprueba que `client.get_rate_limit_status()["is_rate_limited"]` pasa a `True` automáticamente.
  - Un test unitario simula un `401` en `update_project` (`PUT`) y `delete_project` (`DELETE`) y verifica que intenta `_handle_401()` y reintenta la operación.

---

### SPEC-P0-03: Desbloqueo del Event Loop en FastAPI (`server/app.py`)
- **Archivo:** `server/app.py`.
- **Problema:** 28 rutas están definidas como `async def` pero ejecutan llamadas bloqueantes de `requests` y `time.sleep()`, congelando el event loop de FastAPI durante generaciones y descargas.
- **Especificación de la solución:**
  1. Cambiar las rutas que invocan métodos síncronos de `get_client()` de `async def` a `def` estándar (FastAPI ejecuta automáticamente las rutas `def` en un threadpool externo sin bloquear el event loop), **manteniendo** `async def` únicamente en:
     - `root()` (`GET /`)
     - `tts()` (`POST /api/v1/tts`, que ya usa `await synthesize_speech_async(...)`)
     - `parse_mj()` y `validate_p()` (cómputo puro en memoria, < 1 ms).
  2. En `download_media`, utilizar `try ... finally:` para garantizar que el archivo temporal (`tempfile.NamedTemporaryFile`) se elimine del disco incluso si `download_video` o `download_image` lanzan una excepción.
  3. Actualizar `version="1.3.0"` a `version=__version__` importado desde `vibes_api`.
- **Criterio de Aceptación:**
  - Las llamadas concurrentes a `/health` responden inmediatamente incluso mientras `/api/v1/videos/generate` está haciendo polling en otro hilo.
  - Ningún archivo temporal queda huérfano en `/tmp` si falla una descarga.

---

### SPEC-P0-04: Corrección del Filtro `--type` y Choices de `aspect-ratio` en la CLI
- **Archivo:** `vibes_api/cli.py`.
- **Problema:**
  1. `cmd_ingredients_list` (línea 185) no pasa `ingredient_type=args.type` a `client.list_ingredients(...)`.
  2. Los subcomandos `videos generate`, `images generate` y `one-shot` listan `"4:5"`, `"3:2"`, `"2:3"` en `choices`, los cuales son rechazados por el servidor de `vibes.ai`.
- **Especificación de la solución:**
  1. Actualizar `cmd_ingredients_list` a:
     ```python
     items = client.list_ingredients(
         owner_filter=args.owner_filter,
         ingredient_type=getattr(args, "type", None),
     )
     ```
  2. Restringir `choices` de `--aspect-ratio` en la CLI a `["1:1", "9:16", "16:9"]`.
- **Criterio de Aceptación:**
  - `vibes-api ingredients list --type CHARACTER` pasa `ingredient_type="CHARACTER"` a `VibesClient.list_ingredients`.

---

## 3. Especificaciones de Paridad y Mejora Funcional (P1)

### SPEC-P1-01: Paridad v1.6.0 en el Servidor FastAPI (`server/app.py`)
Agregar al servidor REST los endpoints que ya existen en `VibesClient` pero faltan en `server/app.py`:
1. **Animación y Regeneración:**
   - `POST /api/v1/images/animate` → `client.animate_image(...)`
   - `POST /api/v1/batches/{bid}/regenerate` → `client.regenerate_batch(...)`
2. **Historial de Revisiones (`v1.6.0`):**
   - `GET /api/v1/revisions` → `client.list_revisions(entity_type, entity_id, limit, offset)`
   - `POST /api/v1/revisions` → `client.save_revision(...)`
3. **Librería Multimedia e Ingredientes (`v1.6.0`):**
   - `GET /api/v1/media/{item_id}` → `client.get_media_item(item_id)`
   - `GET /api/v1/media/{item_id}/projects` → `client.list_media_item_projects(item_id)`
   - `POST /api/v1/media/{item_id}/favorite` → `client.favorite_content_item(item_id)`
   - `GET /api/v1/ingredients/{iid}/projects` → `client.list_ingredient_projects(iid)`
   - `POST /api/v1/ingredients/bulk-delete` → `client.bulk_delete_ingredients(ids)`
   - `PUT /api/v1/ingredients/{iid}` → `client.update_ingredient(iid, ...)`
4. **Moodboards CRUD completo:**
   - `GET /api/v1/moodboards/{mid}`, `POST /api/v1/moodboards`, `PATCH /api/v1/moodboards/{mid}`, `DELETE /api/v1/moodboards/{mid}`
5. **Streaming SSE en Timeline Chat:**
   - Añadir parámetro `stream: bool = False` (o endpoint `POST /api/v1/timeline/chat/stream`) que retorne un `StreamingResponse(..., media_type="text/event-stream")` para clientes que deseen eventos en tiempo real.

### SPEC-P1-02: Paridad v1.6.0 en la CLI (`vibes_api/cli.py`)
Agregar subcomandos en `vibes-api` para las funciones incorporadas en `v1.6.0`:
- `vibes-api revisions list --entity-type project --entity-id <id>`
- `vibes-api revisions save --entity-type project --entity-id <id> --label "Snapshot"`
- `vibes-api media get <item_id>`
- `vibes-api media favorite <item_id>`
- `vibes-api media projects <item_id>`
- `vibes-api ingredients projects <ingredient_id>`
- `vibes-api ingredients bulk-delete <id1> <id2> ...`
- `vibes-api last-export <project_id>`

---

## 4. Especificaciones de Refactorización y Rendimiento (P2)

### SPEC-P2-01: Optimización de `list_media_by_ingredient` con Caché de Lotes
- **Archivo:** `vibes_api/client.py` (líneas 4212–4245).
- **Problema:** Si 4 videos provienen del mismo `batch_id`, `list_media_by_ingredient` realiza 4 peticiones `GET /api/generation-batches/{batch_id}` idénticas.
- **Especificación de la solución:**
  - Mantener un diccionario local `batch_cache: Dict[str, dict] = {}` durante la ejecución del método para consultar cada `batch_id` como máximo **una sola vez**, reduciendo hasta en un 75% las llamadas HTTP redundantes.

### SPEC-P2-02: Modularización Progresiva de `vibes_api/client.py`
- Dividir `vibes_api/client.py` siguiendo la estructura de `_transport.py` y `_mixins/` definida en `SDD/02_ARCHITECTURE_SPEC.md`, manteniendo todas las exportaciones públicas intactas en `vibes_api/client.py` y `vibes_api/__init__.py`.

### SPEC-P2-03: Actualización de `FEATURE_MATRIX.md`
- Sincronizar las tablas de las secciones 1–33 y la tabla de estadísticas de `FEATURE_MATRIX.md` con el estado real de `v1.6.0` (donde Timeline Composition, Publishing, Rate Limit, Midjourney Parser, Resumable/Batch Uploads, Revisions y Analytics ya están implementados), reflejando la cobertura real (> 90% de las funciones aplicables a un cliente API).

---

## 5. Especificaciones Evolutivas hacia `v2.0.0` (P3)

1. **Auto-Resize de Imágenes con Pillow (`upload_image_file`):**
   - Si Pillow (`PIL`) está instalado y el usuario pasa `auto_resize: bool = True` en `upload_image_file()`, redimensionar automáticamente imágenes que superen `4096×4096` píxeles manteniendo la relación de aspecto (`Image.LANCZOS`) y recomprimir si superan `10 MB`, replicando el comportamiento del `<canvas>` en la interfaz web de `vibes.ai`.
2. **Cliente Asíncrono Nativo (`AsyncVibesClient`):**
   - Basado en `httpx.AsyncClient` para integraciones de alto rendimiento en servidores ASGI/FastAPI y pipelines concurrentes de generación multimedia.

