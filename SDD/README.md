# SDD — Specification-Driven Development (`VibesAI-api`)

Esta carpeta contiene la **Constitución** y las **Especificaciones Técnicas y Funcionales** del proyecto **VibesAI-api** (`v1.6.0`). Su propósito es guiar el mantenimiento, corrección de defectos y evolución arquitectónica bajo un enfoque de *Specification-Driven Development (SDD)*.

## 📚 Índice de Documentos

1. **[00_CONSTITUTION.md](./00_CONSTITUTION.md) — Constitución del Proyecto**
   - Principios arquitectónicos innegociables (Paridad de protocolo con `vibes.ai`, Resiliencia por diseño, Concurrencia sin bloqueo, Modularidad SRP y Sincronización SDK ↔ CLI ↔ Server).
   - Estándares de calidad de código, manejo de secretos/cookies y mandato de pruebas offline.
2. **[01_SYSTEM_ANALYSIS.md](./01_SYSTEM_ANALYSIS.md) — Análisis Técnico Completo del Sistema**
   - Inventario detallado de todos los módulos (`vibes_api`, `server`, `tests`, `Dockerfile`).
   - **8 Hallazgos Críticos / Bugs detectados** en la auditoría de código (métodos duplicados en `client.py`, sistema de rate-limiting `429` desconectado, bloqueo del event loop asíncrono en FastAPI, bug en filtro `--type` de la CLI, etc.).
   - Fortalezas clave de la arquitectura actual.
3. **[02_ARCHITECTURE_SPEC.md](./02_ARCHITECTURE_SPEC.md) — Especificación de Arquitectura**
   - Diagrama de componentes y dependencias.
   - Plan de modularización de `vibes_api/client.py` (4,487 líneas) mediante `_transport.py` y `_mixins/` con 100% de retrocompatibilidad.
   - Pipeline unificado de peticiones HTTP (`_request`) con control de `429` y auto-relogin OIDC (`401`) en todos los verbos HTTP.
   - Arquitectura de concurrencia para FastAPI (`server/app.py`).
4. **[03_API_AND_DOMAIN_SPEC.md](./03_API_AND_DOMAIN_SPEC.md) — Especificación Funcional y de Dominio**
   - Ciclo de vida de autenticación y flujo OIDC (`VIBES_META_SESSION` + `AUTH_META_COOKIES`).
   - Protocolo de generación en 2 pasos, estructura de identificadores (`UUID v7`, `mg_request_id`, prefijos de lote) y límites de validación.
   - Contratos de Ingredientes, cadena de fallback TTS (`vibes` → `edge-tts` → `gtts`) e invariantes del motor `Composition`.
5. **[04_IMPROVEMENT_ROADMAP_SPEC.md](./04_IMPROVEMENT_ROADMAP_SPEC.md) — Especificación de Mejoras y Plan de Acción**
   - Backlog priorizado (`P0` a `P3`) con especificaciones concretas (`SPEC-P0-01` a `SPEC-P2-03`) y criterios de aceptación verificables para ejecutar las mejoras necesarias.

