"""NapCat utilities: system dependency check, architecture detection, download helper."""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path


# ---------------------------------------------------------------------------
# Architecture detection
# ---------------------------------------------------------------------------

def detect_arch() -> str:
    """Return the NapCat release architecture suffix.

    Returns one of: ``x64``, ``arm64``.
    Raises ``RuntimeError`` on unsupported architectures.
    """
    machine = platform.machine().lower()
    if machine in ("x86_64", "amd64"):
        return "x64"
    if machine in ("aarch64", "arm64"):
        return "arm64"
    raise RuntimeError(f"Unsupported architecture: {machine}")


def is_linux() -> bool:
    """Return True if running on Linux."""
    return sys.platform == "linux"


# ---------------------------------------------------------------------------
# System dependency check
# ---------------------------------------------------------------------------

# (command, package_name_apt, package_name_dnf, description)
_REQUIRED_DEPS = [
    ("unzip", "unzip", "unzip", "解压 NapCat.Shell.zip"),
    ("curl", "curl", "curl", "下载文件"),
    ("g++", "g++", "gcc-c++", "编译 NapCat launcher"),
    ("Xvfb", "xvfb", "xorg-x11-server-Xvfb", "虚拟帧缓冲（无头运行 QQ）"),
    ("qq", None, None, "LinuxQQ（将由 setup 安装）"),
]


def check_deps() -> list[tuple[str, str, bool]]:
    """Check required system dependencies.

    Returns a list of ``(name, description, available)`` tuples.
    """
    results: list[tuple[str, str, bool]] = []
    for cmd, _apt, _dnf, desc in _REQUIRED_DEPS:
        if cmd == "qq":
            # QQ is installed by setup, not expected pre-installed
            continue
        available = shutil.which(cmd) is not None
        results.append((cmd, desc, available))
    return results


def missing_deps() -> list[tuple[str, str]]:
    """Return only the missing dependencies as ``(name, description)``."""
    return [(name, desc) for name, desc, ok in check_deps() if not ok]


def suggest_install_commands(missing: list[tuple[str, str]]) -> str:
    """Generate install suggestions for missing deps."""
    if not missing:
        return ""
    lines = ["缺少系统依赖，请先安装：", ""]
    names = [name for name, _ in missing]
    lines.append(f"  sudo apt install {' '.join(names)}")
    lines.append(f"  # 或")
    lines.append(f"  sudo dnf install {' '.join(names)}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Download helper
# ---------------------------------------------------------------------------

def download_file(url: str, dest: Path, *, desc: str = "") -> None:
    """Download a file with a simple progress indicator.

    Raises on HTTP errors or write failures.
    """
    if dest.exists():
        print(f"  ✓ 已存在: {dest.name}")
        return

    label = desc or dest.name
    print(f"  ↓ 下载 {label}...")

    tmp = dest.with_suffix(dest.suffix + ".tmp")
    try:
        urllib.request.urlretrieve(url, tmp, reporthook=_progress_hook(label))
        tmp.rename(dest)
        print()  # newline after progress
    except Exception:
        tmp.unlink(missing_ok=True)
        raise


def _progress_hook(label: str):
    """Return a urlretrieve reporthook that prints a simple progress bar."""
    last_pct = [-1]

    def hook(block_num: int, block_size: int, total_size: int) -> None:
        if total_size <= 0:
            return
        downloaded = block_num * block_size
        pct = min(100, int(downloaded * 100 / total_size))
        if pct != last_pct[0]:
            bar = "█" * (pct // 2) + "░" * (50 - pct // 2)
            print(f"\r  {label} [{bar}] {pct}%", end="", flush=True)
            last_pct[0] = pct

    return hook


# ---------------------------------------------------------------------------
# GitHub release URL resolution
# ---------------------------------------------------------------------------

def get_latest_napcat_download_url() -> str:
    """Resolve the latest NapCat.Shell.zip download URL from GitHub releases.

    Uses the GitHub API to find the latest release asset named
    ``NapCat.Shell.zip``.
    """
    import json

    api_url = "https://api.github.com/repos/NapNeko/NapCatQQ/releases/latest"
    print("  → 查询 NapCat 最新版本...")

    req = urllib.request.Request(api_url, headers={"User-Agent": "lingxuan/0.1"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        data = json.loads(resp.read())

    tag = data.get("tag_name", "unknown")
    print(f"  → 最新版本: {tag}")

    for asset in data.get("assets", []):
        name = asset.get("name", "")
        if name == "NapCat.Shell.zip":
            url = asset.get("browser_download_url", "")
            if url:
                return url

    raise RuntimeError("在最新 release 中未找到 NapCat.Shell.zip")


def get_launcher_cpp_url() -> str:
    """Return the raw URL for napcat-linux-launcher's launcher.cpp."""
    return (
        "https://raw.githubusercontent.com/NapNeko/napcat-linux-launcher/main/launcher.cpp"
    )


def get_linuxqq_download_url() -> str:
    """Return the LinuxQQ .deb download URL for the current architecture.

    Uses Tencent's CDN (same pin as NapCat-Docker).
    """
    arch = detect_arch()
    deb_arch = "amd64" if arch == "x64" else "arm64"
    return (
        "https://qqdl.gtimg.cn/qqfile/QQNT/9.9.32/beta/fd40a3ec/"
        f"linuxqq_3.2.30-50969_{deb_arch}.deb"
    )
