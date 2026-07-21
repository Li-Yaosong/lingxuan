"""NapCat installer: download, LinuxQQ install, launcher compile.

Implements the ``lingxuan napcat setup`` workflow:
1. Check system dependencies
2. Download NapCat.Shell.zip from GitHub releases
3. Install LinuxQQ (deb/rpm)
4. Download and compile napcat-linux-launcher (libnapcat_launcher.so)
5. Generate NapCat config files (onebot11 + webui)
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

from lingxuan.napcat.config import write_configs
from lingxuan.napcat.utils import (
    download_file,
    get_latest_napcat_download_url,
    get_launcher_cpp_url,
    get_linuxqq_download_url,
    is_linux,
    missing_deps,
    suggest_install_commands,
)


# LinuxQQ .deb runtime dependencies (Debian/Ubuntu)
_LINUXQQ_DEB_DEPS = [
    "libgtk-3-0",
    "libnotify4",
    "libxss1",
    "libxtst6",
    "xdg-utils",
    "libsecret-1-0",
]

_SYSTEM_QQ_BIN = Path("/opt/QQ/qq")


def _run_privileged(cmd: list[str]) -> subprocess.CompletedProcess:
    """Run a command as root, prefixing with sudo when needed."""
    if os.geteuid() != 0:
        cmd = ["sudo", *cmd]
    return subprocess.run(cmd, check=True, capture_output=True)


def _linuxqq_installed() -> bool:
    """Return True when LinuxQQ is available on the system."""
    return _SYSTEM_QQ_BIN.exists() or shutil.which("qq") is not None


class SetupError(RuntimeError):
    """Raised when NapCat setup fails."""


def run_setup(
    *,
    napcat_dir: Path,
    qq_dir: Path,
    ws_url: str,
) -> None:
    """Run the full NapCat setup workflow.

    Arguments:
        napcat_dir: Directory to install NapCat into.
        qq_dir: Directory to install LinuxQQ into.
        ws_url: Reverse WebSocket URL for NapCat to connect to lingxuan.
    """
    if not is_linux():
        raise SetupError("NapCat 裸机安装仅支持 Linux")

    print("=" * 55)
    print("  灵轩 NapCat 安装向导")
    print("=" * 55)

    # Step 0: System dependency check
    _check_system_deps()

    # Step 1: Download NapCat.Shell.zip
    napcat_dir.mkdir(parents=True, exist_ok=True)
    _download_napcat(napcat_dir)

    # Step 2: Install LinuxQQ
    qq_dir.mkdir(parents=True, exist_ok=True)
    _install_linuxqq(qq_dir)

    # Step 3: Compile launcher
    _compile_launcher(napcat_dir, qq_dir)

    # Step 4: Generate config
    _generate_config(napcat_dir, ws_url)

    print()
    print("=" * 55)
    print("  ✓ NapCat 安装完成！")
    print("=" * 55)
    print()
    print("  下一步：")
    print(f"    lingxuan napcat start    # 启动 NapCat，扫码登录")
    print()


# ---------------------------------------------------------------------------
# Step 0: System dependency check
# ---------------------------------------------------------------------------

def _check_system_deps() -> None:
    """Check for required system tools; abort if missing."""
    missing = missing_deps()
    if not missing:
        return

    print()
    print(suggest_install_commands(missing))
    raise SetupError("请先安装缺少的系统依赖后重新运行 setup")


# ---------------------------------------------------------------------------
# Step 1: Download NapCat
# ---------------------------------------------------------------------------

def _download_napcat(napcat_dir: Path) -> None:
    """Download and extract NapCat.Shell.zip."""
    print()
    print("── Step 1/4: 下载 NapCat ──")

    shell_zip = napcat_dir / "NapCat.Shell.zip"

    if shell_zip.exists():
        print(f"  ✓ 已存在: {shell_zip}")
    else:
        url = get_latest_napcat_download_url()
        download_file(url, shell_zip, desc="NapCat.Shell.zip")

    # Extract
    shell_dir = napcat_dir / "NapCat.Shell"
    if shell_dir.exists():
        print(f"  ✓ 已解压: {shell_dir}")
    else:
        print("  → 解压 NapCat.Shell.zip...")
        shutil.unpack_archive(str(shell_zip), str(shell_dir))
        print("  ✓ 解压完成")

    # Create napcat/ symlink inside NapCat.Shell/ so that
    # loadNapCat.js can resolve ./napcat/napcat.mjs correctly.
    # The zip puts napcat.mjs at the root level, but loadNapCat.js
    # expects it at ./napcat/napcat.mjs (relative to its own location).
    napcat_symlink = shell_dir / "napcat"
    if napcat_symlink.is_symlink() and napcat_symlink.resolve() == shell_dir.resolve():
        print(f"  ✓ napcat/ 软链接: {napcat_symlink}")
    else:
        napcat_symlink.unlink(missing_ok=True)
        napcat_symlink.symlink_to(".", target_is_directory=True)
        print(f"  ✓ 创建 napcat/ 软链接 → {shell_dir}")


# ---------------------------------------------------------------------------
# Step 2: Install LinuxQQ
# ---------------------------------------------------------------------------

def _install_linuxqq(qq_dir: Path) -> None:
    """Install LinuxQQ from Tencent's official package."""
    print()
    print("── Step 2/4: 安装 LinuxQQ ──")

    # Check if QQ is already installed system-wide
    if _linuxqq_installed():
        print("  ✓ 系统已安装 LinuxQQ")
        return

    # Check if we've already installed it locally
    local_qq_bin = qq_dir / "usr" / "bin" / "qq"
    local_qq_bin_clean = qq_dir / "bin" / "qq"
    if local_qq_bin_clean.exists() or local_qq_bin.exists():
        print("  ✓ 本地已安装 LinuxQQ")
        return

    # Try to install via system package manager
    try:
        url = get_linuxqq_download_url()
    except RuntimeError as e:
        print(f"  ⚠ {e}")
        print("  → 请手动安装 LinuxQQ: https://im.qq.com/linuxqq/index.shtml")
        return

    deb_path = qq_dir / "linuxqq.deb"
    download_file(url, deb_path, desc="LinuxQQ deb")

    # Install with apt when available so dependencies are resolved correctly.
    print("  → 安装 LinuxQQ...")
    try:
        if shutil.which("apt-get"):
            _run_privileged(
                ["apt-get", "install", "-y", "--no-install-recommends", *_LINUXQQ_DEB_DEPS]
            )
            _run_privileged(["apt-get", "install", "-y", str(deb_path.resolve())])
        else:
            _run_privileged(["dpkg", "-i", str(deb_path)])
        if not _linuxqq_installed():
            raise SetupError("LinuxQQ 安装后仍找不到 /opt/QQ/qq")
        print("  ✓ LinuxQQ 安装完成（系统级）")
    except (subprocess.CalledProcessError, FileNotFoundError) as exc:
        print("  ⚠ 自动安装失败，请手动安装:")
        if os.geteuid() == 0:
            print(f"    apt-get install -y {deb_path}")
        else:
            print(f"    sudo apt-get install -y {deb_path}")
        raise SetupError(f"LinuxQQ 安装失败: {exc}") from exc


# ---------------------------------------------------------------------------
# Step 3: Compile launcher
# ---------------------------------------------------------------------------

def _compile_launcher(napcat_dir: Path, qq_dir: Path) -> None:
    """Download and compile the napcat-linux-launcher."""
    print()
    print("── Step 3/4: 编译 NapCat Launcher ──")

    so_path = napcat_dir / "libnapcat_launcher.so"
    if so_path.exists():
        print(f"  ✓ 已编译: {so_path}")
        return

    # Download launcher.cpp
    cpp_path = napcat_dir / "launcher.cpp"
    launcher_url = get_launcher_cpp_url()
    download_file(launcher_url, cpp_path, desc="launcher.cpp")

    # Compile
    print("  → 编译 libnapcat_launcher.so...")
    try:
        subprocess.run(
            [
                "g++", "-shared", "-fPIC",
                "-o", str(so_path),
                str(cpp_path),
                "-ldl",
            ],
            check=True,
            capture_output=True,
        )
        print(f"  ✓ 编译完成: {so_path}")
    except subprocess.CalledProcessError as e:
        stderr = e.stderr.decode(errors="replace") if e.stderr else ""
        raise SetupError(f"编译 launcher 失败: {stderr}")


# ---------------------------------------------------------------------------
# Step 4: Generate config
# ---------------------------------------------------------------------------

def _generate_config(napcat_dir: Path, ws_url: str) -> None:
    """Generate NapCat configuration files."""
    print()
    print("── Step 4/4: 生成配置文件 ──")

    config_dir = napcat_dir / "config"
    written = write_configs(config_dir, ws_url)

    for path in written:
        print(f"  ✓ 写入: {path}")

    print(f"  → 反向 WS 地址: {ws_url}")
