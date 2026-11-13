"""
Platform detection for backend selection.
"""

import platform
from typing import Literal


# Platform tokens for which we actually build and publish downloadable GPU
# server assets. Anything else must not be offered a download (it would 404).
SUPPORTED_GPU_ASSET_PLATFORMS = ("linux-x86_64", "windows-x86_64")


def server_asset_platform() -> str:
    """Platform token identifying which downloadable GPU server assets to fetch.

    Windows and Linux CUDA builds are distinct binaries published as separate
    release assets, so the download URL must be qualified by platform.
    """
    machine = platform.machine().lower()
    arch = "arm64" if machine in ("arm64", "aarch64") else "x86_64"
    return f"{platform.system().lower()}-{arch}"


def get_backend_type() -> Literal["pytorch"]:
    """Every engine runs on PyTorch (CUDA when present, else CPU)."""
    return "pytorch"
