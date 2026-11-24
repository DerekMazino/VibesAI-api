# Especificación Funcional y de Dominio: VibesAI-api

> **Documento:** `03_API_AND_DOMAIN_SPEC.md`  
> **Estado:** Especificación de Contratos y Protocolos  
> **Versión de Referencia:** `v1.6.0` (Paridad con bundles de Next.js de `vibes.ai` — Nov 2026)

---

## 1. Subsistema de Autenticación y Ciclo de Vida de Sesión

### 1.1 Credenciales Soportadas
| Credencial | Origen | Obligatoria | Propósito |
|---|---|---|---|
| `VIBES_META_SESSION` | Cookie `meta_session` en `https://vibes.ai` | **Sí** | Token principal de sesión (`<uuid>.<firma>`). Se envía en el header `Cookie: meta_session=<val>;cookie_ack=true`. |
| `AUTH_META_COOKIES` | Cookies de `https://auth.meta.com` (`ps_n`, `ps_l`, `datr`, `fs`, `locale`) | No (Recomendada) | Permite ejecutar el flujo OIDC automático cuando `meta_session` expira (~30 min de inactividad). |
| `VIBES_COOKIE_FILE` | Ruta local (ej. `/data/.vibes_cookie`) | No | Persistencia en disco de la última cookie `meta_session` rotada para sobrevivir reinicios del servidor. |
| `VIBES_API_KEY` | Definida por el operador del servidor FastAPI | No | Protege los endpoints `/api/v1/*` del servidor REST mediante `X-API-Key` o `Authorization: Bearer`. |

### 1.2 Protocolo de Auto Re-login OIDC (`_attempt_oidc_relogin`)
Cuando una petición devuelve `HTTP 401`:
1. **Paso 1 (`vibes.ai`):** `GET /api/meta-oidc/start` (sin redirección automática, solo con `Cookie: cookie_ack=true`). Extrae `Location` (hacia `auth.meta.com`) y el token `oauth_csrf_token` del header `Set-Cookie`.
2. **Paso 2 (`auth.meta.com`):** `GET <redirect_url>` enviando únicamente `Cookie: <AUTH_META_COOKIES>`. Si responde `302/307` con `code=` en `Location`, continúa; si responde `400` (rate limit o cookies de Meta expiradas), aborta inmediatamente sin reintentar para no agravar el bloqueo.
3. **Paso 3 (`auth.meta.ai/ecto`):** `GET <redirect2>` para intercambio de código OIDC → redirige de vuelta a `vibes.ai`.
4. **Paso 4 (`vibes.ai` callback):** `GET <redirect3>` enviando `Cookie: cookie_ack=true;oauth_csrf_token=<csrf_token>`. Extrae la nueva `meta_session` de `Set-Cookie` (o sigue el redirect final en el Paso 5 si la cookie se emite allí).
5. **Paso 5 (Restauración):** Actualiza `self.session.headers["Cookie"]`, invoca el callback `on_cookie_refresh(new_cookie)` y reintenta la petición original.

---

## 2. Subsistema de Generación Multimedia (Two-Step Batch Protocol)

Todas las generaciones en `vibes.ai` siguen un patrón de dos fases (creación de lote + disparo de generación):

### 2.1 Formato de Identificadores de Lote (`batchId`) y Trazabilidad
- **Trazabilidad obligatoria:** Todo POST a `/api/generate/*` debe incluir:
  - `"mg_request_id": f"www-{_uuid_v7()}"`
  - `"projectId": project_id`
- **Formatos de `batchId` según el flujo:**
  | Flujo | `generationType` | Modelo por defecto | Formato de `batchId` | Modo de respuesta |
  |---|---|---|---|---|
  | Text-to-Video | `t2v` | `midjen-short` | `batch-{uuid_v7}` | Asíncrono (Polling / SSE) |
  | Image-to-Video (Frames) | `i2v` | `midjen-short` | `batch-{uuid_v7}` | Asíncrono (Polling / SSE) |
  | Image Animate | `i2v` | `midjen-short` | `image2video-{ms}-{hex8}` | Asíncrono (Polling / SSE) |
  | Video Extend | `extend` | `midjen-extend` | `extend-{ms}-{hex8}` | Asíncrono (Polling / SSE) |
  | Video Edit (v2v) | `v2v` | `midjen-video-edit` | `video2video-{ms}-{hex8}` | Asíncrono (Polling / SSE) |
  | Text-to-Image | `t2i` | `midjen-base` | `batch-{uuid_v7}` | **Síncrono** (retorna URLs en `data[]`) |
  | Lip Sync | `lipsync` | `midjen` / `heygen-avatar-iv` | Generado por el servidor (`data.batchId`) | Asíncrono (Polling / SSE) |

### 2.2 Restricciones de Dominio Verificadas
- **Relaciones de Aspecto (`AspectRatio`):** Únicamente `"1:1"` (1280×1280), `"9:16"` (720×1280) y `"16:9"` (1280×720).
- **Resoluciones (`Resolution`):** `"480p"` y `"720p"`.
- **Variaciones:** Entre `1` y `4` por lote.
- **Límites de Validación Local:**
  - Longitud máxima de prompt: `10,000` caracteres.
  - Longitud máxima de nombre de proyecto: `255` caracteres.
  - Nombre de usuario: `3` a `30` caracteres.
  - Tamaño máximo de imagen subida: `10 MB` y `4096×4096` píxeles (máximo 12 archivos por lote).
  - Duración máxima de clip musical: `60,000 ms` (general) o `9,000 ms` (clip corto).

---

## 3. Subsistema de Ingredientes y Moodboards

### 3.1 Tipos de Ingredientes (`IngredientType`)
1. `CHARACTER` ("Character" en UI) → se vincula internamente vía `orefImageHandle`.
2. `STYLE` ("Style" en UI) → se vincula internamente vía `srefImageHandle`.
3. `SETTING` ("Scene" en UI) → se vincula internamente vía `settingImageHandles`.

### 3.2 Modos de Referencia en Generación
- **Existente (`IngredientRef.by_id`):** Se inserta en `config.ingredients[]` con `{ingredientId, ingredientType, name, imageUrl}`.
- **Creación inline por imagen subida (`CreateIngredient.by_image_ent_id`):** Se inserta en `config.createIngredients[]` con `{sourceImageEntId, ingredientType, name, imageUrl}`.
- **Creación inline por nombre (`CreateIngredient.by_name`):** Se inserta en `config.createIngredients[]` con `{ingredientType, name}`.
- **Actualización de Ingredientes:** Utiliza el proxy GraphQL `POST /api/meta-graphql` con `doc_id = "26515982254723441"` (`UpdateIngredientMutation`).
- **Borrado Masivo:** `bulk_delete_ingredients([ids])` enruta un único ID a `DELETE /api/studio/ingredients/{id}` y múltiples IDs a `POST /api/studio/ingredients/bulk-delete`.

---

## 4. Subsistema de Síntesis de Voz (`tts_fallback.py`)

### 4.1 Cadena de Motores y Resolución de Voces
El método `client.tts(text, voice, ...)` y el endpoint `POST /api/v1/tts` implementan una cadena de 3 niveles controlada por el modo (`auto` | `off` | `only`):
1. **Motor 1 (`vibes`):** `POST /api/studio/playai/tts` (41 voces `play_ai_*`). Propenso a errores `502 / 403 Facebook expired access token` cuando rota el token interno de Meta.
2. **Motor 2 (`edge-tts`):** Síntesis neuronal de Microsoft Edge (paquete opcional `edge-tts`). Se activa automáticamente si el Motor 1 falla o si se solicita explícitamente un ID neuronal (`^[a-z]{2,3}-[A-Za-z]{2,}-[A-Za-z]+Neural$`).
3. **Motor 3 (`gtts`):** Google Translate TTS vía HTTP `requests` estándar (sin dependencias adicionales, divide textos en fragmentos de ≤ 190 caracteres respetando puntuación y concatena los frames MP3). Se activa si Edge TTS no está instalado/falla o si se pide una voz `gtts-<lang>`.

### 4.2 Contrato de Respuesta TTS
```json
{
  "audioBase64": "<base64-mp3>",
  "contentType": "audio/mpeg",
  "engine": "vibes | edge-tts | gtts",
  "voice": "<resolved-voice-id>"
}
```

---

## 5. Subsistema de Composición de Línea de Tiempo (`Composition`)

La clase `Composition` (`vibes_api/composition.py`) manipula el árbol JSON del proyecto antes de persistirlo con `PUT /api/projects/{id}` o exportarlo a MP4 con `POST /api/projects/{id}/timeline/download` (síncrono) o `POST /api/projects/{id}/timeline/export-surfguard` (asíncrono).

### 5.1 Estructura de Datos
- **Composición:** `{"id": "studio-composition", "tracks": [...], "duration": float}`
- **Tipos de Pista (`track.type`):** `"video"`, `"text"`, `"uploaded-audio"`, `"library-music"`.
- **IDs de Pista y Clip:** Generados con `{prefix}-{ms_timestamp}-{base36(7)}`.
- **Invariantes del Motor:**
  - `resize_clip`: La duración nunca puede superar `sourceDuration`.
  - `set_fade`: `fadeIn` y `fadeOut` se limitan automáticamente a `clip.duration / 2`.
  - `set_volume`: El volumen se acota al rango `[0.0, 1.0]`.
  - `set_speed`: El rango válido es `[0.1, 10.0]` y recalcula inversamente `clip.duration = old_duration * (old_speed / new_speed)`.
  - `split_clip`: Divide un clip en `at_time` (debe cumplir `start < at_time < start + duration`), ajusta `trimStart` del segundo segmento y reordena los ítems de la pista por `start`.

