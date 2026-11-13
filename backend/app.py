"""FastAPI application factory, middleware, and lifecycle events."""

import asyncio
import logging
import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path


class ColoredFormatter(logging.Formatter):
    """Custom formatter to add colors matching uvicorn's style."""

    COLORS = {
        "DEBUG": "\033[36m",  # Cyan
        "INFO": "\033[32m",  # Green
        "WARNING": "\033[33m",  # Yellow
        "ERROR": "\033[31m",  # Red
        "CRITICAL": "\033[35m",  # Magenta
    }
    RESET = "\033[0m"

    def format(self, record):
        log_color = self.COLORS.get(record.levelname, self.RESET)
        record.levelname = f"{log_color}{record.levelname}{self.RESET}"
        return super().format(record)


# Configure logging to match uvicorn's format with colors
handler = logging.StreamHandler(sys.stderr)
handler.setFormatter(ColoredFormatter("%(levelname)s:     %(message)s"))
logging.basicConfig(
    level=logging.INFO,
    handlers=[handler],
)

logger = logging.getLogger(__name__)

if getattr(sys, "frozen", False) and os.name == "nt":
    # PyInstaller keeps one copy of a DLL shared by several wheels, in whichever
    # *.libs folder it met first (onnx's C++ runtime ends up under pandas.libs).
    for _libs in Path(sys._MEIPASS).glob("*.libs"):
        os.add_dll_directory(str(_libs))

from .utils import hf_pin  # noqa: E402

hf_pin.install()

import torch
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from . import __version__, config, database
from .services import tts, transcribe, llm
from .database import get_db
from .utils.platform_detect import get_backend_type
from .utils.progress import get_progress_manager
# Re-exported for backwards compatibility with callers importing it from here.
from .utils.http import safe_content_disposition as safe_content_disposition
from .services.task_queue import create_background_task, init_queue
from .routes import register_routers


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    from .mcp_server.server import build_mcp_server, compose_lifespan
    from .mcp_server.context import ClientIdMiddleware

    # Build the MCP app up-front so we can wire its lifespan into FastAPI's —
    # FastMCP's Streamable HTTP transport only works if its session manager
    # runs inside the parent ASGI lifespan.
    mcp = build_mcp_server()
    mcp_app = mcp.http_app(path="/", transport="http")

    @asynccontextmanager
    async def voicebox_lifespan(app: FastAPI):
        await _run_startup(app)
        try:
            yield
        finally:
            # Paired with _run_startup via try/finally: runs whether or
            # not the nested MCP lifespan entered cleanly, so a partial
            # startup still unloads whatever models were loaded.
            await _run_shutdown()

    # compose_lifespan enters factories in order (voicebox startup →
    # MCP startup) and exits in LIFO (MCP teardown first → models
    # unload last). That ordering matters on shutdown: FastMCP's
    # __aexit__ cancels in-flight session tasks, and we want that to
    # happen *before* _run_shutdown yanks the TTS / Whisper / LLM
    # models out from under any MCP request that was still generating.
    lifespan = compose_lifespan(voicebox_lifespan, mcp_app.router.lifespan_context)

    application = FastAPI(
        title="voicebox API",
        description="Production-quality Qwen3-TTS voice cloning API",
        version=__version__,
        lifespan=lifespan,
    )

    _configure_cors(application)
    application.add_middleware(ClientIdMiddleware)
    register_routers(application)
    application.mount("/mcp", mcp_app)
    logger.info("MCP: mounted at /mcp")
    _mount_frontend(application)

    return application


def _configure_cors(application: FastAPI) -> None:
    """Set up CORS middleware with local-first defaults."""
    default_origins = [
        "http://localhost:5173",  # Vite dev server
        "http://127.0.0.1:5173",
        "http://localhost:17493",
        "http://127.0.0.1:17493",
        "tauri://localhost",  # Tauri webview (macOS)
        "https://tauri.localhost",  # Tauri webview (Windows/Linux)
        "http://tauri.localhost",  # Tauri webview (Windows, some builds)
    ]
    env_origins = os.environ.get("VOICEBOX_CORS_ORIGINS", "")
    all_origins = default_origins + [o.strip() for o in env_origins.split(",") if o.strip()]

    application.add_middleware(
        CORSMiddleware,
        allow_origins=all_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )


def _mount_frontend(application: FastAPI) -> None:
    """Serve the built web frontend when present (Docker / web deployment).

    The Dockerfile copies the Vite build output to ``/app/frontend/``.  When
    that directory exists we mount static assets and add a catch-all route so
    the React SPA handles client-side routing.  In dev or API-only mode the
    directory is absent and this function is a no-op.
    """
    frontend_dir = Path(__file__).resolve().parent.parent / "frontend"
    if not frontend_dir.is_dir():
        return

    from fastapi.staticfiles import StaticFiles
    from fastapi.responses import FileResponse

    # Mount hashed assets (JS, CSS, images) that Vite places under /assets
    assets_dir = frontend_dir / "assets"
    if assets_dir.is_dir():
        application.mount(
            "/assets",
            StaticFiles(directory=str(assets_dir)),
            name="frontend-assets",
        )

    # SPA catch-all: serve files if they exist, otherwise index.html for
    # client-side routes like /voices, /stories, /models, etc.
    @application.get("/{full_path:path}")
    async def serve_spa(full_path: str):
        file_path = (frontend_dir / full_path).resolve()
        # Guard against path traversal — only serve files inside frontend_dir
        if full_path and file_path.is_file() and file_path.is_relative_to(frontend_dir):
            return FileResponse(file_path)
        return FileResponse(frontend_dir / "index.html", media_type="text/html")

    logger.info("Frontend: serving SPA from %s", frontend_dir)


def _get_gpu_status() -> str:
    """Return a human-readable string describing GPU availability."""
    if torch.cuda.is_available():
        from .backends.base import check_cuda_compatibility

        device_name = torch.cuda.get_device_name(0)
        compatible, _warning = check_cuda_compatibility()
        label = f"CUDA ({device_name})"
        if not compatible:
            label += " [UNSUPPORTED - see logs]"
        return label
    elif hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
        return "MPS (Apple Silicon)"

    # Intel XPU (Arc / Data Center) via IPEX
    try:
        import intel_extension_for_pytorch  # noqa: F401

        if hasattr(torch, "xpu") and torch.xpu.is_available():
            try:
                xpu_name = torch.xpu.get_device_name(0)
            except Exception:
                xpu_name = "Intel GPU"
            return f"XPU ({xpu_name})"
    except ImportError:
        pass

    return "None (CPU only)"


async def _run_startup(application: FastAPI) -> None:
    """Database init, warnings, model-cache prep. Runs on lifespan entry."""
    import platform
    import sys

    logger.info("Voice Studio Box v%s starting up", __version__)
    logger.info(
        "Python %s on %s %s (%s)",
        sys.version.split()[0],
        platform.system(),
        platform.release(),
        platform.machine(),
    )

    database.init_db()

    from .database.session import _db_path

    logger.info("Database: %s", _db_path)
    logger.info("Data directory: %s", config.get_data_dir())

    init_queue()

    # Mark stale "generating" records as failed -- leftovers from a killed process
    from sqlalchemy import text as sa_text

    db = next(get_db())
    try:
        result = db.execute(
            sa_text(
                "UPDATE generations SET status = 'failed', "
                "error = 'Server was shut down during generation' "
                "WHERE status IN ('generating', 'loading_model')"
            )
        )
        if result.rowcount > 0:
            logger.info("Marked %d stale generation(s) as failed", result.rowcount)

        from .database import VoiceProfile as DBVoiceProfile, Generation as DBGeneration

        profile_count = db.query(DBVoiceProfile).count()
        generation_count = db.query(DBGeneration).count()
        logger.info("Profiles: %d, Generations: %d", profile_count, generation_count)

        db.commit()
    except Exception as e:
        db.rollback()
        logger.warning("Could not clean up stale generations: %s", e)
    finally:
        db.close()

    # Seed the LLM backend dispatch from the persisted capture settings so
    # the very first refinement / personality call after startup honours a
    # user-configured custom OpenAI-compatible endpoint without needing a
    # settings-write to flip the module state first.
    db = next(get_db())
    try:
        from .services.settings import bootstrap_llm_backend_config

        bootstrap_llm_backend_config(db)
    except Exception as e:
        logger.warning("Could not bootstrap custom LLM config: %s", e)
    finally:
        db.close()

    backend_type = get_backend_type()
    logger.info("Backend: %s", backend_type.upper())
    logger.info("GPU: %s", _get_gpu_status())

    from .backends.base import check_cuda_compatibility

    _compatible, _cuda_warning = check_cuda_compatibility()
    if not _compatible:
        logger.warning("GPU COMPATIBILITY: %s", _cuda_warning)

    from .services.cuda import check_and_update_cuda_binary

    create_background_task(check_and_update_cuda_binary())

    from .services.dubbing import recover_interrupted
    from .services.model_updates import sync_pins
    from .services.vram import idle_watcher

    try:
        sync_pins()
    except Exception as e:
        logger.warning('Could not apply model version pins: %s', e)
    recover_interrupted()
    from .services.voice_lab import clear_stale_jobs

    clear_stale_jobs()
    create_background_task(idle_watcher())

    try:
        progress_manager = get_progress_manager()
        progress_manager._set_main_loop(asyncio.get_running_loop())
    except Exception as e:
        logger.warning("Could not initialize progress manager event loop: %s", e)

    try:
        from huggingface_hub import constants as hf_constants

        cache_dir = Path(hf_constants.HF_HUB_CACHE)
        cache_dir.mkdir(parents=True, exist_ok=True)
        logger.info("Model cache: %s", cache_dir)
    except Exception as e:
        logger.warning("Could not create HuggingFace cache directory: %s", e)

    logger.info("Ready")


async def _run_shutdown() -> None:
    """Unload models on lifespan exit."""
    logger.info("Voice Studio Box server shutting down...")
    try:
        tts.unload_tts_model()
    except Exception:
        logger.exception("Failed to unload TTS model")
    try:
        transcribe.unload_whisper_model()
    except Exception:
        logger.exception("Failed to unload Whisper model")
    try:
        llm.unload_llm_model()
    except Exception:
        logger.exception("Failed to unload LLM model")


app = create_app()
