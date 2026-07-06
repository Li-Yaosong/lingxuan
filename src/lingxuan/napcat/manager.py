"""NapCat process manager: Xvfb + LD_PRELOAD lifecycle.

Manages the NapCatQQ process lifecycle:
- Start Xvfb virtual framebuffer
- Launch QQ with LD_PRELOAD injection
- Stop via PID file + SIGTERM
- Status check via PID file
- Log file access
"""

from __future__ import annotations

import os
import signal
import subprocess
import time
from pathlib import Path


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_XVFB_DISPLAY = 99
_PID_FILE = "napcat.pid"
_LOGS_DIR = "logs"


class NapCatManager:
    """Manage NapCat process lifecycle.

    Args:
        napcat_dir: NapCat installation directory (contains launcher.so, config/).
        qq_dir: LinuxQQ installation directory.
    """

    def __init__(self, napcat_dir: Path, qq_dir: Path) -> None:
        self._napcat_dir = napcat_dir
        self._qq_dir = qq_dir
        self._pid_file = napcat_dir / _PID_FILE
        self._logs_dir = napcat_dir / _LOGS_DIR
        self._xvfb_proc: subprocess.Popen | None = None
        self._qq_proc: subprocess.Popen | None = None

    # ── start ──────────────────────────────────────────────────────────

    def start(self, *, foreground: bool = False) -> None:
        """Start NapCat (Xvfb + QQ with LD_PRELOAD).

        In foreground mode, logs stream to the console (useful for QR code).
        In background mode, logs go to the logs directory.
        """
        if self.is_running():
            print("NapCat 已在运行中 (PID {})".format(self._read_pid()))
            return

        self._logs_dir.mkdir(parents=True, exist_ok=True)

        # 1. Start Xvfb
        print("→ 启动 Xvfb...")
        self._start_xvfb()

        # 2. Launch QQ with LD_PRELOAD
        print("→ 启动 NapCat (LD_PRELOAD)...")
        self._start_qq(foreground=foreground)

        pid = self._qq_proc.pid if self._qq_proc else -1
        self._write_pid(pid)
        print(f"✓ NapCat 已启动 (PID {pid})")

        if foreground:
            print()
            print("── NapCat 日志 ──")
            print("  扫码登录后，NapCat 将自动连接灵轩。")
            print("  按 Ctrl+C 停止 NapCat。")
            print()

    # ── stop ───────────────────────────────────────────────────────────

    def stop(self) -> None:
        """Stop NapCat and Xvfb processes."""
        pid = self._read_pid()
        if pid is None:
            print("NapCat 未在运行（无 PID 文件）")
            return

        try:
            os.kill(pid, signal.SIGTERM)
            print(f"→ 发送 SIGTERM 到进程 {pid}")
        except ProcessLookupError:
            print(f"进程 {pid} 已不存在")

        # Also stop Xvfb if we started it
        if self._xvfb_proc is not None:
            self._xvfb_proc.terminate()
            self._xvfb_proc = None

        # Wait briefly for the process to exit
        for _ in range(10):
            try:
                os.kill(pid, 0)
                time.sleep(0.5)
            except ProcessLookupError:
                break

        self._pid_file.unlink(missing_ok=True)
        self._qq_proc = None
        print("✓ NapCat 已停止")

    # ── status ─────────────────────────────────────────────────────────

    def is_running(self) -> bool:
        """Check if NapCat is currently running."""
        pid = self._read_pid()
        if pid is None:
            return False
        try:
            os.kill(pid, 0)
            return True
        except ProcessLookupError:
            # Stale PID file
            self._pid_file.unlink(missing_ok=True)
            return False

    def status(self) -> dict:
        """Return status info as a dict."""
        pid = self._read_pid()
        running = self.is_running()
        return {
            "running": running,
            "pid": pid if running else None,
            "napcat_dir": str(self._napcat_dir),
            "qq_dir": str(self._qq_dir),
        }

    # ── logs ───────────────────────────────────────────────────────────

    def get_latest_log_path(self) -> Path | None:
        """Return the path to the most recent NapCat log file."""
        if not self._logs_dir.exists():
            return None
        log_files = sorted(self._logs_dir.glob("*.log"), key=lambda p: p.stat().st_mtime)
        return log_files[-1] if log_files else None

    def tail_logs(self, lines: int = 50) -> str:
        """Return the last N lines of the most recent log."""
        log_path = self.get_latest_log_path()
        if log_path is None:
            return "(无日志文件)"
        content = log_path.read_text(encoding="utf-8", errors="replace")
        all_lines = content.splitlines()
        return "\n".join(all_lines[-lines:])

    # ── internal helpers ───────────────────────────────────────────────

    def _start_xvfb(self) -> None:
        """Start Xvfb virtual framebuffer and wait for it to be ready."""
        self._xvfb_proc = subprocess.Popen(
            [
                "Xvfb",
                f":{_XVFB_DISPLAY}",
                "-screen", "0", "1x1x8",
                "+extension", "GLX",
                "+render",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        # Wait for Xvfb to be ready by checking if the display socket exists
        import tempfile
        x_lock = Path(tempfile.gettempdir()) / f".X{_XVFB_DISPLAY}-lock"
        for _ in range(50):  # up to 5 seconds
            if x_lock.exists():
                break
            time.sleep(0.1)
        else:
            # Fallback: just wait a bit
            time.sleep(0.5)

    def _start_qq(self, *, foreground: bool = False) -> None:
        """Launch QQ with the NapCat LD_PRELOAD launcher."""
        launcher_so = self._napcat_dir / "libnapcat_launcher.so"
        if not launcher_so.exists():
            raise RuntimeError(f"找不到 launcher: {launcher_so}")

        # Find QQ binary
        qq_bin = self._find_qq_binary()
        if qq_bin is None:
            raise RuntimeError(
                "找不到 QQ 可执行文件。请确认 LinuxQQ 已安装。"
            )

        env = os.environ.copy()
        env["DISPLAY"] = f":{_XVFB_DISPLAY}"
        env["LD_PRELOAD"] = str(launcher_so.resolve())
        env["NAPCAT_WORKDIR"] = str(self._napcat_dir.resolve())

        # NAPCAT_BOOTMAIN tells loadNapCat.js where to find napcat.mjs.
        # Without this, it resolves ./napcat/napcat.mjs relative to the
        # project root (where the top-level loadNapCat.js lives), which
        # doesn't exist.  The actual NapCat shell lives in NapCat.Shell/.
        # Must use resolve() to get an absolute path — Node's path.join
        # treats relative paths as relative to CWD, not the env var value.
        napcat_shell = self._napcat_dir / "NapCat.Shell"
        if napcat_shell.is_dir():
            env["NAPCAT_BOOTMAIN"] = str(napcat_shell.resolve())

        if foreground:
            self._qq_proc = subprocess.Popen(
                [str(qq_bin), "--no-sandbox"],
                env=env,
            )
        else:
            log_path = self._logs_dir / f"napcat-{int(time.time())}.log"
            log_file = open(log_path, "w", encoding="utf-8")
            self._qq_proc = subprocess.Popen(
                [str(qq_bin), "--no-sandbox"],
                env=env,
                stdout=log_file,
                stderr=subprocess.STDOUT,
            )

    def _find_qq_binary(self) -> Path | None:
        """Find the QQ executable in system or local install."""
        # Check system-wide first
        system_qq = shutil_which("qq")
        if system_qq:
            return Path(system_qq)

        # Check common local paths
        candidates = [
            Path("/opt/QQ/qq"),
            self._qq_dir / "qq",
            Path("/usr/bin/qq"),
        ]
        for p in candidates:
            if p.exists():
                return p

        return None

    def _read_pid(self) -> int | None:
        """Read the PID from the PID file."""
        if not self._pid_file.exists():
            return None
        try:
            return int(self._pid_file.read_text().strip())
        except (ValueError, OSError):
            return None

    def _write_pid(self, pid: int) -> None:
        """Write the PID to the PID file."""
        self._pid_file.write_text(str(pid))


# ---------------------------------------------------------------------------
# Standalone helpers
# ---------------------------------------------------------------------------

def shutil_which(name: str) -> str | None:
    """Find a command on PATH."""
    import shutil
    return shutil.which(name)


def build_manager_from_config(config: "ConfigProvider") -> NapCatManager:
    """Construct a NapCatManager from ConfigProvider settings."""
    from pathlib import Path

    napcat_dir = Path(config.get_str("NAPCAT_DIR"))
    qq_dir = Path(config.get_str("NAPCAT_QQ_DIR"))

    return NapCatManager(napcat_dir=napcat_dir, qq_dir=qq_dir)
