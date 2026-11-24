"""FastAPI server exposing VibesClient as a REST API."""
from __future__ import annotations

import json
import logging
import os
import sys
import tempfile
import threading
from typing import Any, Dict, List, Optional

from fastapi import Depends, FastAPI, Header, HTTPException, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from vibes_api import VibesAPIError, VibesClient, __version__
from vibes_api.tts_fallback import (
    EDGE_VOICE_CATALOG,
    GTTS_LANGUAGES,
    synthesize_speech_async,
)

logger = logging.getLogger("vibes_api.server")

_client: Optional[VibesClient] = None
_lock = threading.Lock()


def get_client() -> VibesClient:
    global _client
    if _client is None:
        with _lock:
            if _client is None:
                ms = os.environ.get("VIBES_META_SESSION")
                if not ms:
                    raise HTTPException(500, "VIBES_META_SESSION env var not set.")
                cf = os.environ.get("VIBES_COOKIE_FILE")
                if cf and os.path.exists(cf):
                    try:
                        with open(cf, encoding="utf-8") as f:
                            ms = f.read().strip()
                    except OSError as exc:
                        logger.warning("Failed to read VIBES_COOKIE_FILE %s: %s", cf, exc)

                def save(c: str) -> None:
                    if cf:
                        try:
                            with open(cf, "w", encoding="utf-8") as f:
                                f.write(c)
                        except OSError as exc:
                            logger.warning("Failed to persist cookie to %s: %s", cf, exc)

                amc = os.environ.get("AUTH_META_COOKIES", "").strip()
                _client = VibesClient(
                    meta_session=ms,
                    auto_refresh=True,
                    background_refresh=True,
                    refresh_interval=float(os.environ.get("VIBES_REFRESH_INTERVAL", "1500")),
                    on_cookie_refresh=save,
                    auto_relogin=bool(amc),
                    auth_meta_cookies=amc or None,
                )
    return _client


def verify_api_key(
    x_api_key: Optional[str] = Header(None, alias="X-API-Key"),
    authorization: Optional[str] = Header(None),
) -> None:
    exp = os.environ.get("VIBES_API_KEY")
    if not exp:
        return
    prov = x_api_key or (
        authorization[7:] if authorization and authorization.startswith("Bearer ") else None
    )
    if prov != exp:
        raise HTTPException(401, "Invalid or missing API key.")


def _err(e: Exception) -> None:
    if isinstance(e, VibesAPIError):
        raise HTTPException(
            e.status or 500,
            detail={"error": str(e), "code": e.code, "response": e.response},
        )
    raise HTTPException(500, str(e))


# ---------------------------------------------------------------------------
# Request schemas (semantic names + backward-compatible short aliases)
# ---------------------------------------------------------------------------

class ProjectCreateRequest(BaseModel):
    name: str = "Untitled"
    composition: Optional[dict] = None


class ProjectUpdateRequest(BaseModel):
    name: Optional[str] = None
    composition: Optional[dict] = None


class VideoGenerateRequest(BaseModel):
    project_id: str
    prompt: str
    aspect_ratio: str = "9:16"
    resolution: str = "480p"
    variations: int = 4
    video_model: str = "midjen-short"
    image_model: str = "midjen-base"
    prompt_model: str = "gemini-2.5-flash"
    ingredients: Optional[List[dict]] = None
    create_ingredients: Optional[List[dict]] = None
    start_frame: Optional[dict] = None
    end_frame: Optional[dict] = None
    moodboard: Optional[dict] = None
    poll: bool = True
    poll_timeout: float = 300.0


class ImageGenerateRequest(BaseModel):
    project_id: str
    prompt: str
    aspect_ratio: str = "1:1"
    resolution: str = "480p"
    variations: int = 1
    image_model: str = "midjen-base"
    prompt_model: str = "gemini-2.5-flash"
    ingredients: Optional[List[dict]] = None
    create_ingredients: Optional[List[dict]] = None
    moodboard: Optional[dict] = None


class ImageEditRequest(BaseModel):
    source_image_ent_id: str
    edit_prompt: str
    project_id: Optional[str] = None


class ImageAnimateRequest(BaseModel):
    project_id: str
    source_image: dict
    prompt: Optional[str] = None
    poll: bool = True
    poll_timeout: float = 300.0


class BatchRegenerateRequest(BaseModel):
    project_id: str
    prompt: Optional[str] = None
    poll: bool = True
    poll_timeout: float = 300.0


class PromptEnhanceRequest(BaseModel):
    prompt: str
    project_id: Optional[str] = None
    batch_type: str = "videos"


class TTSRequest(BaseModel):
    text: str
    voice: str
    output_format: str = "mp3"
    language: Optional[str] = None


class LipSyncRequest(BaseModel):
    project_id: str
    image_prompt: str
    script: str
    audio_url: str
    audio_duration_ms: int
    engine: str = "midjen"
    ingredients: Optional[List[dict]] = None
    aspect_ratio: Optional[str] = None
    music_track: Optional[dict] = None
    custom_motion_prompt: Optional[str] = None


class VideoExtendRequest(BaseModel):
    project_id: str
    batch_id: str
    content_id: Optional[str] = None
    prompt: Optional[str] = None
    poll: bool = True
    poll_timeout: float = 300.0


class VideoEditRequest(BaseModel):
    project_id: str
    batch_id: str
    content_id: Optional[str] = None
    prompt: str
    poll: bool = True
    poll_timeout: float = 300.0


class ShareLinkRequest(BaseModel):
    entity_type: str
    entity_id: str
    expires_at: Optional[str] = None
    max_uses: Optional[int] = None


class IngredientCreateRequest(BaseModel):
    name: str
    ingredient_type: str
    source_image_ent_id: Optional[str] = None
    image_url: Optional[str] = None
    description: Optional[str] = None


class IngredientUpdateRequest(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    personality: Optional[str] = None
    backstory: Optional[str] = None
    core_beliefs: Optional[str] = None
    image_uri: Optional[str] = None


class BulkDeleteIngredientsRequest(BaseModel):
    ingredient_ids: List[str]


class MoodboardCreateRequest(BaseModel):
    name: str
    moodboard_code: str
    images: List[dict]


class MoodboardUpdateRequest(BaseModel):
    name: Optional[str] = None
    add_images: Optional[List[dict]] = None
    remove_images: Optional[List[str]] = None


class RevisionSaveRequest(BaseModel):
    entity_type: str
    entity_id: str
    snapshot: dict
    label: str = "Session end"


class TimelineChatRequest(BaseModel):
    input: str
    instructions: Optional[str] = None
    tools: Optional[List[dict]] = None
    composition: Optional[dict] = None


class TimelineExportRequest(BaseModel):
    composition: dict


class PublishRequest(BaseModel):
    content_item_id: str
    batch_id: Optional[str] = None
    caption: Optional[str] = None
    audio_types: Optional[List[str]] = None
    prompt: Optional[str] = None
    image_prompt: Optional[str] = None
    video_prompt: Optional[str] = None


class PromptTextRequest(BaseModel):
    prompt: str


class UploadImageRequest(BaseModel):
    image_base64: str


# Backward-compatible aliases
PC = ProjectCreateRequest
PU = ProjectUpdateRequest
VG = VideoGenerateRequest
IG = ImageGenerateRequest
IE = ImageEditRequest
PE = PromptEnhanceRequest
TT = TTSRequest
LR = LipSyncRequest
EV = VideoExtendRequest
EDV = VideoEditRequest
SL = ShareLinkRequest
IC = IngredientCreateRequest
TC = TimelineChatRequest
TE = TimelineExportRequest
PR = PublishRequest
PM = PromptTextRequest
UI = UploadImageRequest

app = FastAPI(title="VibesAI API", version=__version__, docs_url="/docs", redoc_url="/redoc")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
AUTH = [Depends(verify_api_key)]


@app.get("/", tags=["Health"])
async def root() -> Dict[str, str]:
    return {"name": "VibesAI API", "version": __version__, "docs": "/docs"}


@app.get("/health", tags=["Health"])
def health() -> Dict[str, Any]:
    try:
        u = get_client().get_me()
        return {"status": "healthy", "user": u.get("username")}
    except Exception as e:
        return {"status": "unhealthy", "error": str(e)}


@app.get("/api/v1/me", tags=["Auth"], dependencies=AUTH)
def me() -> dict:
    try:
        return get_client().get_me()
    except Exception as e:
        _err(e)


@app.get("/api/v1/check-token", tags=["Auth"], dependencies=AUTH)
def check_token() -> Dict[str, bool]:
    try:
        return {"valid": get_client().check_token()}
    except Exception as e:
        _err(e)


@app.get("/api/v1/current-cookie", tags=["Auth"], dependencies=AUTH)
def current_cookie() -> Dict[str, str]:
    return {"meta_session": get_client().get_current_cookie()}


@app.get("/api/v1/projects", tags=["Projects"], dependencies=AUTH)
def list_projects(
    limit: int = 25,
    offset: int = 0,
    sort: str = "newest",
    search: Optional[str] = None,
) -> dict:
    try:
        return get_client().list_projects(limit=limit, offset=offset, sort=sort, search=search)
    except Exception as e:
        _err(e)


@app.post("/api/v1/projects", tags=["Projects"], dependencies=AUTH)
def create_project(b: ProjectCreateRequest) -> dict:
    try:
        return get_client().create_project(name=b.name, composition=b.composition)
    except Exception as e:
        _err(e)


@app.get("/api/v1/projects/{pid}", tags=["Projects"], dependencies=AUTH)
def get_project(pid: str) -> dict:
    try:
        return get_client().get_project(pid)
    except Exception as e:
        _err(e)


@app.put("/api/v1/projects/{pid}", tags=["Projects"], dependencies=AUTH)
def update_project(pid: str, b: ProjectUpdateRequest) -> dict:
    try:
        return get_client().update_project(pid, name=b.name, composition=b.composition)
    except Exception as e:
        _err(e)


@app.delete("/api/v1/projects/{pid}", tags=["Projects"], dependencies=AUTH)
def delete_project(pid: str, delete_assets: bool = False) -> Dict[str, bool]:
    try:
        get_client().delete_project(pid, delete_assets=delete_assets)
        return {"success": True}
    except Exception as e:
        _err(e)


@app.post("/api/v1/videos/generate", tags=["Videos"], dependencies=AUTH)
def gen_video(b: VideoGenerateRequest) -> dict:
    try:
        return get_client().generate_video(
            project_id=b.project_id,
            prompt=b.prompt,
            aspect_ratio=b.aspect_ratio,
            resolution=b.resolution,
            variations=b.variations,
            video_model=b.video_model,
            image_model=b.image_model,
            prompt_model=b.prompt_model,
            ingredients=b.ingredients,
            create_ingredients=b.create_ingredients,
            start_frame=b.start_frame,
            end_frame=b.end_frame,
            moodboard=b.moodboard,
            poll=b.poll,
            poll_timeout=b.poll_timeout,
        )
    except Exception as e:
        _err(e)


@app.post("/api/v1/videos/extend", tags=["Videos"], dependencies=AUTH)
def ext_video(b: VideoExtendRequest) -> dict:
    try:
        c = get_client()
        batch = c.get_batch(b.batch_id)
        src = next(
            (x for x in batch.get("content", []) if x["id"] == b.content_id), None
        ) or (batch["content"][0] if batch.get("content") else None)
        if not src:
            raise HTTPException(404, "No content item found")
        return c.extend_video(
            project_id=b.project_id,
            source_video=src,
            prompt=b.prompt,
            poll=b.poll,
            poll_timeout=b.poll_timeout,
        )
    except HTTPException:
        raise
    except Exception as e:
        _err(e)


@app.post("/api/v1/videos/edit", tags=["Videos"], dependencies=AUTH)
def edit_video(b: VideoEditRequest) -> dict:
    try:
        c = get_client()
        batch = c.get_batch(b.batch_id)
        src = next(
            (x for x in batch.get("content", []) if x["id"] == b.content_id), None
        ) or (batch["content"][0] if batch.get("content") else None)
        if not src:
            raise HTTPException(404, "No content item found")
        return c.edit_video(
            project_id=b.project_id,
            source_video=src,
            prompt=b.prompt,
            poll=b.poll,
            poll_timeout=b.poll_timeout,
        )
    except HTTPException:
        raise
    except Exception as e:
        _err(e)


@app.post("/api/v1/images/generate", tags=["Images"], dependencies=AUTH)
def gen_image(b: ImageGenerateRequest) -> dict:
    try:
        return get_client().generate_image(
            project_id=b.project_id,
            prompt=b.prompt,
            aspect_ratio=b.aspect_ratio,
            resolution=b.resolution,
            variations=b.variations,
            image_model=b.image_model,
            prompt_model=b.prompt_model,
            ingredients=b.ingredients,
            create_ingredients=b.create_ingredients,
            moodboard=b.moodboard,
        )
    except Exception as e:
        _err(e)


@app.post("/api/v1/images/edit", tags=["Images"], dependencies=AUTH)
def edit_image(b: ImageEditRequest) -> dict:
    try:
        return get_client().edit_image(
            source_image_ent_id=b.source_image_ent_id,
            edit_prompt=b.edit_prompt,
            project_id=b.project_id,
        )
    except Exception as e:
        _err(e)


@app.post("/api/v1/images/animate", tags=["Images"], dependencies=AUTH)
def animate_image(b: ImageAnimateRequest) -> dict:
    try:
        return get_client().animate_image(
            project_id=b.project_id,
            source_image=b.source_image,
            prompt=b.prompt,
            poll=b.poll,
            poll_timeout=b.poll_timeout,
        )
    except Exception as e:
        _err(e)


@app.post("/api/v1/upload/image", tags=["Uploads"], dependencies=AUTH)
def upload_image(b: UploadImageRequest) -> dict:
    try:
        return get_client().upload_image(b.image_base64)
    except Exception as e:
        _err(e)


@app.post("/api/v1/prompts/enhance", tags=["Prompts"], dependencies=AUTH)
def enhance_prompt(b: PromptEnhanceRequest) -> Dict[str, Any]:
    try:
        return {
            "variations": get_client().enhance_prompt(
                prompt=b.prompt, project_id=b.project_id, batch_type=b.batch_type
            )
        }
    except Exception as e:
        _err(e)


@app.get("/api/v1/voices", tags=["TTS"], dependencies=AUTH)
def list_voices() -> Dict[str, Any]:
    """Voice catalog across every TTS engine."""
    fallback = (
        [{**v, "engine": "edge-tts"} for v in EDGE_VOICE_CATALOG]
        + [{**v, "engine": "gtts"} for v in GTTS_LANGUAGES]
    )
    try:
        voices = get_client().list_voices()
        return {
            "voices": [
                {
                    "id": v.get("id"),
                    "name": v.get("name"),
                    "description": v.get("description"),
                    "sample": v.get("sample"),
                    "engine": "vibes",
                }
                for v in voices
            ]
            + fallback,
        }
    except Exception:
        return {"voices": fallback}


@app.post("/api/v1/tts", tags=["TTS"], dependencies=AUTH)
async def tts(b: TTSRequest) -> dict:
    """Synthesize speech with the fallback chain (env ``VIBES_TTS_FALLBACK``)."""
    mode = os.environ.get("VIBES_TTS_FALLBACK", "auto")
    try:
        return await synthesize_speech_async(
            get_client(),
            text=b.text,
            voice=b.voice,
            output_format=b.output_format,
            language=b.language,
            mode=mode,
        )
    except Exception as e:
        _err(e)


@app.get("/api/v1/media", tags=["Media"], dependencies=AUTH)
def list_media(
    limit: int = 50,
    offset: int = 0,
    type: Optional[str] = None,
    search: Optional[str] = None,
) -> dict:
    try:
        return get_client().list_media(limit=limit, offset=offset, type=type, search=search)
    except Exception as e:
        _err(e)


@app.get("/api/v1/media/{item_id}", tags=["Media"], dependencies=AUTH)
def get_media_item(item_id: str) -> dict:
    try:
        return get_client().get_media_item(item_id)
    except Exception as e:
        _err(e)


@app.get("/api/v1/media/{item_id}/projects", tags=["Media"], dependencies=AUTH)
def list_media_item_projects(item_id: str) -> Dict[str, Any]:
    try:
        return {"projects": get_client().list_media_item_projects(item_id)}
    except Exception as e:
        _err(e)


@app.post("/api/v1/media/{item_id}/favorite", tags=["Media"], dependencies=AUTH)
def favorite_media_item(item_id: str) -> dict:
    try:
        return get_client().favorite_content_item(item_id)
    except Exception as e:
        _err(e)


@app.get("/api/v1/media/{item_id}/download", tags=["Media"], dependencies=AUTH)
def download_media(item_id: str, type: str = "video") -> Response:
    path: Optional[str] = None
    try:
        c = get_client()
        suffix = ".mp4" if type == "video" else ".png"
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as f:
            path = f.name
        if type == "video":
            c.download_video(item_id, path)
        else:
            c.download_image(item_id, path)
        with open(path, "rb") as f:
            content = f.read()
        return Response(
            content=content,
            media_type="video/mp4" if type == "video" else "image/png",
        )
    except Exception as e:
        _err(e)
    finally:
        if path and os.path.exists(path):
            try:
                os.unlink(path)
            except OSError as exc:
                logger.warning("Failed to remove temp file %s: %s", path, exc)


@app.delete("/api/v1/media/{item_id}", tags=["Media"], dependencies=AUTH)
def del_media(item_id: str) -> Dict[str, bool]:
    try:
        get_client().delete_content_item(item_id)
        return {"success": True}
    except Exception as e:
        _err(e)


@app.get("/api/v1/batches", tags=["Batches"], dependencies=AUTH)
def list_batches(
    limit: int = 12, offset: int = 0, project_id: Optional[str] = None
) -> dict:
    try:
        return get_client().list_batches(limit=limit, offset=offset, project_id=project_id)
    except Exception as e:
        _err(e)


@app.get("/api/v1/batches/{bid}", tags=["Batches"], dependencies=AUTH)
def get_batch(bid: str) -> dict:
    try:
        return get_client().get_batch(bid)
    except Exception as e:
        _err(e)


@app.post("/api/v1/batches/{bid}/poll", tags=["Batches"], dependencies=AUTH)
def poll_batch(bid: str, timeout: float = 180.0) -> dict:
    try:
        return get_client().poll_batch(bid, timeout=timeout)
    except Exception as e:
        _err(e)


@app.post("/api/v1/batches/{bid}/regenerate", tags=["Batches"], dependencies=AUTH)
def regenerate_batch(bid: str, b: BatchRegenerateRequest) -> dict:
    try:
        return get_client().regenerate_batch(
            project_id=b.project_id,
            batch_id=bid,
            prompt=b.prompt,
            poll=b.poll,
            poll_timeout=b.poll_timeout,
        )
    except Exception as e:
        _err(e)


@app.get("/api/v1/ingredients", tags=["Ingredients"], dependencies=AUTH)
def list_ingredients(
    owner_filter: str = "LIBRARY", ingredient_type: Optional[str] = None
) -> Dict[str, Any]:
    try:
        return {
            "ingredients": get_client().list_ingredients(
                owner_filter=owner_filter, ingredient_type=ingredient_type
            )
        }
    except Exception as e:
        _err(e)


@app.post("/api/v1/ingredients", tags=["Ingredients"], dependencies=AUTH)
def create_ingredient(b: IngredientCreateRequest) -> dict:
    try:
        return get_client().create_ingredient(
            name=b.name,
            ingredient_type=b.ingredient_type,
            source_image_ent_id=b.source_image_ent_id,
            image_url=b.image_url,
            description=b.description,
        )
    except Exception as e:
        _err(e)


@app.put("/api/v1/ingredients/{iid}", tags=["Ingredients"], dependencies=AUTH)
def update_ingredient(iid: str, b: IngredientUpdateRequest) -> dict:
    try:
        return get_client().update_ingredient(
            ingredient_id=iid,
            name=b.name,
            description=b.description,
            personality=b.personality,
            backstory=b.backstory,
            core_beliefs=b.core_beliefs,
            image_uri=b.image_uri,
        )
    except Exception as e:
        _err(e)


@app.get("/api/v1/ingredients/{iid}/projects", tags=["Ingredients"], dependencies=AUTH)
def list_ingredient_projects(iid: str) -> Dict[str, Any]:
    try:
        return {"projects": get_client().list_ingredient_projects(iid)}
    except Exception as e:
        _err(e)


@app.post("/api/v1/ingredients/bulk-delete", tags=["Ingredients"], dependencies=AUTH)
def bulk_delete_ingredients(b: BulkDeleteIngredientsRequest) -> dict:
    try:
        return get_client().bulk_delete_ingredients(b.ingredient_ids)
    except Exception as e:
        _err(e)


@app.delete("/api/v1/ingredients/{iid}", tags=["Ingredients"], dependencies=AUTH)
def del_ingredient(iid: str) -> Dict[str, bool]:
    try:
        get_client().delete_ingredient(iid)
        return {"success": True}
    except Exception as e:
        _err(e)


@app.get("/api/v1/revisions", tags=["Revisions"], dependencies=AUTH)
def list_revisions(
    entity_type: str, entity_id: str, limit: int = 25, offset: int = 0
) -> dict:
    try:
        return get_client().list_revisions(
            entity_type=entity_type, entity_id=entity_id, limit=limit, offset=offset
        )
    except Exception as e:
        _err(e)


@app.post("/api/v1/revisions", tags=["Revisions"], dependencies=AUTH)
def save_revision(b: RevisionSaveRequest) -> dict:
    try:
        return get_client().save_revision(
            entity_type=b.entity_type,
            entity_id=b.entity_id,
            snapshot=b.snapshot,
            label=b.label,
        )
    except Exception as e:
        _err(e)


@app.post("/api/v1/share-links", tags=["Share"], dependencies=AUTH)
def create_share(b: ShareLinkRequest) -> dict:
    try:
        return get_client().create_share_link(
            b.entity_type, b.entity_id, expires_at=b.expires_at, max_uses=b.max_uses
        )
    except Exception as e:
        _err(e)


@app.get("/api/v1/share-links", tags=["Share"], dependencies=AUTH)
def list_shares(entity_type: str, entity_id: str) -> Dict[str, Any]:
    try:
        return {"shareLinks": get_client().list_share_links(entity_type, entity_id)}
    except Exception as e:
        _err(e)


@app.post("/api/v1/timeline/chat", tags=["Timeline"], dependencies=AUTH)
def timeline_chat(b: TimelineChatRequest) -> Dict[str, Any]:
    try:
        events = []
        for ev in get_client().timeline_chat(
            b.input, instructions=b.instructions, tools=b.tools, composition=b.composition
        ):
            events.append(ev)
            if ev.get("type") in ("completed", "error"):
                break
        return {"events": events}
    except Exception as e:
        _err(e)


@app.post("/api/v1/timeline/chat/stream", tags=["Timeline"], dependencies=AUTH)
def timeline_chat_stream(b: TimelineChatRequest) -> StreamingResponse:
    def _event_gen():
        for ev in get_client().timeline_chat(
            b.input, instructions=b.instructions, tools=b.tools, composition=b.composition
        ):
            yield f"data: {json.dumps(ev)}\n\n"
            if ev.get("type") in ("completed", "error"):
                break

    return StreamingResponse(_event_gen(), media_type="text/event-stream")


@app.post("/api/v1/timeline/export", tags=["Timeline"], dependencies=AUTH)
def export_timeline(project_id: str, b: TimelineExportRequest) -> Response:
    try:
        mp4 = get_client().export_timeline(project_id, b.composition)
        return Response(content=mp4, media_type="video/mp4")
    except Exception as e:
        _err(e)


@app.post("/api/v1/publish", tags=["Publishing"], dependencies=AUTH)
def publish(b: PublishRequest) -> dict:
    try:
        return get_client().publish_to_vibes(
            content_item_id=b.content_item_id,
            batch_id=b.batch_id,
            caption=b.caption,
            audio_types=b.audio_types,
            prompt=b.prompt,
            image_prompt=b.image_prompt,
            video_prompt=b.video_prompt,
        )
    except Exception as e:
        _err(e)


@app.post("/api/v1/lipsync", tags=["LipSync"], dependencies=AUTH)
def lipsync(b: LipSyncRequest) -> dict:
    try:
        return get_client().generate_lipsync(
            project_id=b.project_id,
            image_prompt=b.image_prompt,
            script=b.script,
            audio_url=b.audio_url,
            audio_duration_ms=b.audio_duration_ms,
            engine=b.engine,
            ingredients=b.ingredients,
            aspect_ratio=b.aspect_ratio,
            music_track=b.music_track,
            custom_motion_prompt=b.custom_motion_prompt,
        )
    except Exception as e:
        _err(e)


@app.get("/api/v1/music/search", tags=["Music"], dependencies=AUTH)
def search_music(
    q: str = "", limit: int = 30, cursor: Optional[str] = None
) -> dict:
    try:
        return get_client().search_music_filtered(query=q, limit=limit, cursor=cursor)
    except Exception as e:
        _err(e)


@app.get("/api/v1/moodboards", tags=["Moodboards"], dependencies=AUTH)
def moodboards() -> Dict[str, Any]:
    try:
        return {"moodboards": get_client().list_moodboards()}
    except Exception as e:
        _err(e)


@app.get("/api/v1/moodboards/{mid}", tags=["Moodboards"], dependencies=AUTH)
def get_moodboard(mid: str) -> dict:
    try:
        return get_client().get_moodboard(mid)
    except Exception as e:
        _err(e)


@app.post("/api/v1/moodboards", tags=["Moodboards"], dependencies=AUTH)
def create_moodboard(b: MoodboardCreateRequest) -> dict:
    try:
        return get_client().create_moodboard(b.name, b.moodboard_code, b.images)
    except Exception as e:
        _err(e)


@app.patch("/api/v1/moodboards/{mid}", tags=["Moodboards"], dependencies=AUTH)
def update_moodboard(mid: str, b: MoodboardUpdateRequest) -> dict:
    try:
        return get_client().update_moodboard(
            moodboard_id=mid,
            add_images=b.add_images,
            remove_images=b.remove_images,
            name=b.name,
        )
    except Exception as e:
        _err(e)


@app.delete("/api/v1/moodboards/{mid}", tags=["Moodboards"], dependencies=AUTH)
def delete_moodboard(mid: str) -> Dict[str, bool]:
    try:
        get_client().delete_moodboard(mid)
        return {"success": True}
    except Exception as e:
        _err(e)


@app.post("/api/v1/utils/parse-midjourney", tags=["Utils"])
async def parse_mj(b: PromptTextRequest) -> dict:
    return VibesClient.parse_midjourney_params(b.prompt)


@app.post("/api/v1/utils/validate-prompt", tags=["Utils"])
async def validate_p(b: PromptTextRequest) -> dict:
    return VibesClient.validate_prompt_length(b.prompt)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "server.app:app",
        host=os.environ.get("HOST", "0.0.0.0"),
        port=int(os.environ.get("PORT", "8000")),
    )
