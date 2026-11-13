"""
PyInstaller build script for creating standalone Python server binary.

Usage:
    python build_binary.py           # Build default (CPU) server binary
    python build_binary.py --cuda    # Build CUDA-enabled server binary
"""

import PyInstaller.__main__
import argparse
import logging
import os
import platform
import sys
from pathlib import Path

logger = logging.getLogger(__name__)


def _installed_torch_pins() -> list[str]:
    """``torch==X`` etc. for the versions in this venv (local tags like +cu128 dropped)."""
    import subprocess

    code = "import importlib.metadata as m; print(' '.join(m.version(p).split('+')[0] for p in ('torch','torchvision','torchaudio')))"
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    versions = out.stdout.split()
    if out.returncode != 0 or len(versions) != 3:
        return ["torch", "torchvision", "torchaudio"]
    return [f"{name}=={v}" for name, v in zip(("torch", "torchvision", "torchaudio"), versions)]


def build_server(cuda=False):
    """Build Python server as standalone binary.

    Args:
        cuda: If True, build with CUDA support and name the binary
              voicebox-server-cuda instead of voicebox-server.
    """
    backend_dir = Path(__file__).parent

    binary_name = "voicebox-server-cuda" if cuda else "voicebox-server"

    # PyInstaller arguments
    # CUDA builds use --onedir so we can split the output into two archives:
    #   1. Server core (~200-400MB) — versioned with the app
    #   2. GPU libs (~2GB) — versioned independently (only redownloaded on
    #      GPU toolkit / torch major version changes)
    # CPU builds remain --onefile for simplicity.
    pack_mode = "--onedir" if cuda else "--onefile"
    args = [
        "server.py",  # Use server.py as entry point instead of main.py
        pack_mode,
        "--name",
        binary_name,
    ]

    # Hide console window on Windows only. On macOS/Linux the sidecar needs
    # stdout/stderr for Tauri to capture logs.
    if platform.system() == "Windows":
        args.append("--noconsole")

    # numpy 2.x / torch ABI mismatch fix: install memmove fallback for
    # torch.from_numpy() before the app starts. Runtime hooks run after
    # FrozenImporter is registered so frozen torch/numpy are importable.
    # Paths are passed relative to backend_dir because os.chdir(backend_dir)
    # runs before PyInstaller. Absolute paths would get baked into the
    # generated .spec, breaking reproducible builds on other machines / CI.
    args.extend(
        [
            "--runtime-hook",
            "pyi_rth_numpy_compat.py",
            # Stub torch.compiler.disable before transformers imports
            # flex_attention, which otherwise triggers torch._dynamo →
            # torch._numpy._ufuncs and crashes at module load under
            # PyInstaller. See pyi_rth_torch_compiler_disable.py.
            "--runtime-hook",
            "pyi_rth_torch_compiler_disable.py",
            # Per-module collection overrides (e.g. forcing scipy.stats._distn_infrastructure
            # to bundle .py source alongside .pyc so the runtime hook can source-patch it).
            "--additional-hooks-dir",
            "pyi_hooks",
        ]
    )

    # Add local qwen_tts path if specified (for editable installs)
    qwen_tts_path = os.getenv("QWEN_TTS_PATH")
    if qwen_tts_path and Path(qwen_tts_path).exists():
        args.extend(["--paths", str(qwen_tts_path)])
        logger.info("Using local qwen_tts source from: %s", qwen_tts_path)

    # Add common hidden imports
    args.extend(
        [
            "--hidden-import",
            "backend",
            "--hidden-import",
            "backend.main",
            "--hidden-import",
            "backend.config",
            "--hidden-import",
            "backend.database",
            "--hidden-import",
            "backend.models",
            "--hidden-import",
            "backend.services.profiles",
            "--hidden-import",
            "backend.services.history",
            "--hidden-import",
            "backend.services.tts",
            "--hidden-import",
            "backend.services.transcribe",
            "--hidden-import",
            "backend.utils.platform_detect",
            "--hidden-import",
            "backend.backends",
            "--hidden-import",
            "backend.backends.pytorch_backend",
            "--hidden-import",
            "--hidden-import",
            "backend.backends.qwen_voice_design_backend",
            "--hidden-import",
            "backend.utils.audio",
            "--hidden-import",
            "backend.utils.cache",
            "--hidden-import",
            "backend.utils.progress",
            "--hidden-import",
            "backend.utils.hf_progress",
            "--hidden-import",
            "backend.services.cuda",
            "--hidden-import",
            "backend.services.effects",
            "--hidden-import",
            "backend.utils.effects",
            "--hidden-import",
            "backend.services.versions",
            "--hidden-import",
            "backend.services.extras",
            "--hidden-import",
            "backend.services.vram",
            "--hidden-import",
            "backend.services.batch",
            "--hidden-import",
            "backend.routes.extras",
            "--hidden-import",
            "backend.utils.audio_post",
            "--hidden-import",
            "backend.utils.text_rules",
            "--hidden-import",
            "backend.utils.vi_text",
            "--hidden-import",
            "backend.utils.expressive",
            "--hidden-import",
            "backend.services.speech_qc",
            "--collect-all",
            "demucs",
            "--collect-all",
            "df",
            "--collect-all",
            "libdf",
            "--collect-submodules",
            "dora",
            "--collect-all",
            "openunmix",
            "--hidden-import",
            "treetable",
            "--hidden-import",
            "retrying",
            "--hidden-import",
            "submitit",
            "--hidden-import",
            "cloudpickle",
            "--hidden-import",
            "backend.services.speaker",
            "--hidden-import",
            "backend.services.audio_clean",
            "--hidden-import",
            "backend.services.final_check",
            "--hidden-import",
            "backend.services.voice_lab",
            "--hidden-import",
            "backend.services.jobs",
            "--hidden-import",
            "backend.utils.abort",
            "--hidden-import",
            "backend.services.repair",
            "--hidden-import",
            "backend.services.studio_projects",
            "--hidden-import",
            "backend.services.dubbing",
            "--hidden-import",
            "backend.services.history_search",
            "--hidden-import",
            "backend.services.model_updates",
            "--hidden-import",
            "backend.routes.studio",
            "--hidden-import",
            "backend.routes.model_updates",
            "--hidden-import",
            "backend.utils.hf_pin",
            "--hidden-import",
            "backend.utils.audio_quality",
            "--hidden-import",
            "demucs",
            "--hidden-import",
            "demucs.pretrained",
            "--hidden-import",
            "demucs.apply",
            "--hidden-import",
            "demucs.htdemucs",
            "--hidden-import",
            "demucs.states",
            "--hidden-import",
            "dora",
            "--hidden-import",
            "julius",
            "--hidden-import",
            "df",
            "--hidden-import",
            "df.enhance",
            "--hidden-import",
            "libdf",
            "--hidden-import",
            "appdirs",
            "--hidden-import",
            "sklearn.cluster",
            "--hidden-import",
            "backend.services.styled_synthesis",
            "--hidden-import",
            "pyloudnorm",
            "--hidden-import",
            "pedalboard",
            "--hidden-import",
            "chatterbox",
            "--hidden-import",
            "chatterbox.mtl_tts",
            "--hidden-import",
            # chatterbox multilingual uses spacy_pkuseg for Chinese word
            # segmentation, which ships pickled dict files (dicts/default.pkl)
            # and native .so extensions that --hidden-import alone won't bundle.
            "--collect-all",
            "spacy_pkuseg",
            "--hidden-import",
            "torch",
            "--hidden-import",
            "transformers",
            "--hidden-import",
            "fastapi",
            "--hidden-import",
            "uvicorn",
            "--hidden-import",
            "sqlalchemy",
            # librosa uses lazy_loader which generates .pyi stub files at
            # install time and reads them at runtime to discover submodules.
            # --hidden-import alone doesn't bundle the stubs, causing
            # "Cannot load imports from non-existent stub" at runtime.
            "--collect-all",
            "lazy_loader",
            "--collect-all",
            "librosa",
            "--hidden-import",
            "soundfile",
            "--hidden-import",
            "qwen_tts",
            "--hidden-import",
            "qwen_tts.inference",
            "--hidden-import",
            "qwen_tts.inference.qwen3_tts_model",
            "--hidden-import",
            "qwen_tts.inference.qwen3_tts_tokenizer",
            "--hidden-import",
            "qwen_tts.core",
            "--hidden-import",
            "qwen_tts.cli",
            "--copy-metadata",
            "qwen-tts",
            "--copy-metadata",
            "requests",
            "--copy-metadata",
            "transformers",
            "--copy-metadata",
            "huggingface-hub",
            "--copy-metadata",
            "tokenizers",
            "--copy-metadata",
            "safetensors",
            "--copy-metadata",
            "tqdm",
            "--hidden-import",
            "requests",
            # qwen_tts uses inspect.getsource() at runtime to locate
            # modeling_qwen3_tts.py — needs physical .py source files bundled
            "--collect-all",
            "qwen_tts",
            # Fix for pkg_resources and jaraco namespace packages
            "--hidden-import",
            "pkg_resources.extern",
            "--collect-submodules",
            "jaraco",
            # inflect uses typeguard @typechecked which calls inspect.getsource()
            # at import time — needs .py source files, not just .pyc bytecode
            "--collect-all",
            "inflect",
            # perth ships pretrained watermark model files (hparams.yaml, .pth.tar)
            # in perth/perth_net/pretrained/ — needed by chatterbox at runtime
            "--collect-all",
            "perth",
            # OmniVoice — diffusion LM TTS, 600+ languages.
            "--hidden-import",
            "backend.backends.omnivoice_backend",
            # The Higgs Audio V2 codec is vendored from transformers 5.x (we are
            # capped at 4.57.6). Both the shim and the vendored package are
            # imported from inside a function, so name them explicitly.
            "--hidden-import",
            "backend.utils.transformers5_compat",
            "--collect-submodules",
            "backend.vendor",
            "--hidden-import",
            "omnivoice",
            "--hidden-import",
            "omnivoice.models.omnivoice",
            "--hidden-import",
            "omnivoice.utils.audio",
            "--hidden-import",
            "omnivoice.utils.text",
            "--hidden-import",
            "omnivoice.utils.voice_design",
            "--hidden-import",
            "omnivoice.utils.duration",
            "--hidden-import",
            "omnivoice.utils.lang_map",
            # omnivoice/__init__.py calls importlib.metadata.version("omnivoice").
            # It degrades to "0.0.0" without metadata, but ship it anyway.
            "--copy-metadata",
            "omnivoice",
            # The vendored codec builds its two sub-models through
            # AutoModel.from_config(), which transformers resolves at runtime —
            # static analysis cannot see either of them. The parent package is
            # not enough: the weights live in modeling_dac / modeling_hubert,
            # which transformers itself imports lazily.
            "--collect-submodules",
            "transformers.models.dac",
            "--collect-submodules",
            "transformers.models.hubert",
            # Reference-audio preprocessing (silence trimming) in
            # omnivoice.utils.audio.
            "--hidden-import",
            "pydub",
            "--hidden-import",
            "torchaudio",
            "--hidden-import",
            "loguru",
            # MCP server — Streamable-HTTP endpoint and the 4 voicebox.* tools.
            # FastMCP pulls in a chain of deps (mcp, cyclopts, openapi-pydantic,
            # etc.) that don't auto-discover cleanly under PyInstaller, so we
            # collect them whole. Small compared to torch.
            "--hidden-import",
            "backend.mcp_server",
            "--hidden-import",
            "backend.mcp_server.server",
            "--hidden-import",
            "backend.mcp_server.tools",
            "--hidden-import",
            "backend.mcp_server.context",
            "--hidden-import",
            "backend.mcp_server.resolve",
            "--hidden-import",
            "backend.mcp_server.events",
            "--collect-all",
            "fastmcp",
            "--collect-all",
            "mcp",
            "--hidden-import",
            "sse_starlette",
        ]
    )

    if sys.version_info >= (3, 13):
        args.extend(["--hidden-import", "audioop"])

    if cuda:
        logger.info("Building with CUDA support")
        args.extend(["--hidden-import", "torch.cuda", "--hidden-import", "torch.backends.cudnn"])

    # Exclude NVIDIA CUDA packages from non-CUDA builds to keep binary small.
    # When building from a venv with CUDA torch installed, PyInstaller would
    # bundle ~3GB of NVIDIA shared libraries. We exclude both the Python
    # modules and the binary DLLs.
    if not cuda:
        nvidia_packages = [
            "nvidia",
            "nvidia.cublas",
            "nvidia.cuda_cupti",
            "nvidia.cuda_nvrtc",
            "nvidia.cuda_runtime",
            "nvidia.cudnn",
            "nvidia.cufft",
            "nvidia.curand",
            "nvidia.cusolver",
            "nvidia.cusparse",
            "nvidia.nccl",
            "nvidia.nvjitlink",
            "nvidia.nvtx",
        ]
        for pkg in nvidia_packages:
            args.extend(["--exclude-module", pkg])

    dist_dir = str(backend_dir / "dist")
    build_dir = str(backend_dir / "build")

    args.extend(
        [
            "--distpath",
            dist_dir,
            "--workpath",
            build_dir,
            "--noconfirm",
            "--clean",
        ]
    )

    # Change to backend directory
    os.chdir(backend_dir)

    # For CPU builds on Windows, ensure we're using CPU-only torch.
    # If CUDA torch is installed (local dev), swap to CPU torch before
    # building, then restore afterwards. This prevents PyInstaller from bundling
    # GPU libraries into the CPU binary.
    restore_torch = None
    torch_pins = _installed_torch_pins()
    try:
        if not cuda and platform.system() == "Windows":
            import subprocess

            cuda_result = subprocess.run(
                [sys.executable, "-c", "import torch; print(torch.version.cuda or '')"], capture_output=True, text=True
            )

            if cuda_result.stdout.strip():
                restore_torch = "cuda"
                logger.info("CUDA torch detected — installing CPU torch for CPU build...")
                subprocess.run(
                    [
                        sys.executable,
                        "-m",
                        "pip",
                        "install",
                        *torch_pins,
                        "--index-url",
                        "https://download.pytorch.org/whl/cpu",
                        "--force-reinstall",
                        "--no-deps",
                        "-q",
                    ],
                    check=True,
                )
        # Run PyInstaller
        PyInstaller.__main__.run(args)
    finally:
        # Restore torch if we swapped it out (even on build failure)
        if restore_torch == "cuda":
            logger.info("Restoring CUDA torch...")
            import subprocess

            subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "pip",
                    "install",
                    *torch_pins,
                    "--index-url",
                    "https://download.pytorch.org/whl/cu128",
                    "--force-reinstall",
                    "--no-deps",
                    "-q",
                ],
                check=True,
            )


    logger.info("Binary built in %s", backend_dir / "dist" / binary_name)


def build_shim():
    """Build the voicebox-mcp stdio shim as a tiny standalone binary.

    This is the bridge for MCP clients that only speak stdio — it proxies
    JSON-RPC to the main voicebox-server's /mcp endpoint. Keep it small: no
    torch, no ML deps, just httpx + asyncio.
    """
    backend_dir = Path(__file__).parent

    args = [
        "mcp_shim/__main__.py",
        "--onefile",
        "--name",
        "voicebox-mcp",
        # Stdio-only — no console hiding needed on Windows since the parent
        # MCP client is spawning this as a child process and wants stdio.
        "--hidden-import",
        "backend.mcp_shim",
        "--hidden-import",
        "backend.mcp_shim.__main__",
        "--hidden-import",
        "httpx",
        "--hidden-import",
        "httpx._transports.default",
        "--hidden-import",
        "anyio",
        # Exclude everything heavy that httpx/asyncio don't actually need so
        # the binary stays tiny (~15 MB instead of ~400 MB).
        "--exclude-module",
        "torch",
        "--exclude-module",
        "transformers",
        "--exclude-module",
        "qwen_tts",
        "--exclude-module",
        "chatterbox",
        "--exclude-module",
        "librosa",
        "--exclude-module",
        "numba",
        "--exclude-module",
        "numpy",
        "--exclude-module",
        "pedalboard",
        "--exclude-module",
        "fastapi",
        "--exclude-module",
        "uvicorn",
        "--exclude-module",
        "sqlalchemy",
        "--exclude-module",
        "fastmcp",
        "--exclude-module",
        "mcp",
    ]

    dist_dir = str(backend_dir / "dist")
    build_dir = str(backend_dir / "build")
    args.extend(
        [
            "--distpath",
            dist_dir,
            "--workpath",
            build_dir,
            "--noconfirm",
            "--clean",
        ]
    )

    os.chdir(backend_dir)
    PyInstaller.__main__.run(args)
    logger.info("Shim built: %s", backend_dir / "dist" / "voicebox-mcp")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Build voicebox binaries")
    parser.add_argument(
        "--cuda",
        action="store_true",
        help="Build CUDA-enabled binary (voicebox-server-cuda)",
    )
    parser.add_argument(
        "--shim",
        action="store_true",
        help="Build the voicebox-mcp stdio shim binary instead of the server",
    )
    cli_args = parser.parse_args()
    if cli_args.shim:
        build_shim()
    else:
        build_server(cuda=cli_args.cuda)

