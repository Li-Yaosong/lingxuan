"""NapCat process manager: optional Xvfb + LD_PRELOAD lifecycle.

Manages the NapCatQQ process lifecycle:
- Optionally start Xvfb virtual framebuffer (or use desktop DISPLAY)
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
        config: Optional ConfigProvider for reading NAPCAT_* settings.
            When provided, ``NAPCAT_QUICK_ACCOUNT`` and ``NAPCAT_WS_URL``
            are read from it; otherwise ``os.environ`` is used as fallback.
    """

    def __init__(
        self,
        napcat_dir: Path,
        qq_dir: Path,
        *,
        config: "ConfigProvider | None" = None,
    ) -> None:
        self._napcat_dir = napcat_dir
        self._qq_dir = qq_dir
        self._config = config
        self._pid_file = napcat_dir / _PID_FILE
        self._logs_dir = napcat_dir / _LOGS_DIR
        self._xvfb_proc: subprocess.Popen | None = None
        self._qq_proc: subprocess.Popen | None = None
        self._display: str = f":{_XVFB_DISPLAY}"

    def _get_config_str(self, key: str, default: str = "") -> str:
        """Read a config value, falling back to os.environ then default."""
        if self._config is not None:
            try:
                return self._config.get_str(key)
            except KeyError:
                pass
        return os.environ.get(key, default)

    def _get_config_bool(self, key: str, default: bool = False) -> bool:
        """Read a bool config value, falling back to os.environ then default."""
        if self._config is not None:
            try:
                return self._config.get_bool(key)
            except KeyError:
                pass
        raw = os.environ.get(key)
        if raw is None:
            return default
        return raw.strip().lower() in ("1", "true", "yes", "on")

    # ── start ──────────────────────────────────────────────────────────

    def start(self, *, foreground: bool = False) -> None:
        """Start NapCat (optional Xvfb + QQ with LD_PRELOAD).

        In foreground mode, logs stream to the console (useful for QR code).
        In background mode, logs go to the logs directory.
        """
        if self.is_running():
            print("NapCat 已在运行中 (PID {})".format(self._read_pid()))
            return

        self._logs_dir.mkdir(parents=True, exist_ok=True)

        use_xvfb = self._get_config_bool("NAPCAT_USE_XVFB", default=True)
        if use_xvfb:
            print("→ 启动 Xvfb...")
            self._start_xvfb()
            self._display = f":{_XVFB_DISPLAY}"
        else:
            self._display = self._get_config_str("DISPLAY") or os.environ.get("DISPLAY", "")
            if not self._display:
                raise RuntimeError(
                    "NAPCAT_USE_XVFB=false 但未设置 DISPLAY，无法在桌面显示 QQ 窗口。"
                )
            print(f"→ 使用桌面显示 {self._display}（不启动 Xvfb）")

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

    def start_gui_login(self) -> None:
        """Start plain LinuxQQ on the desktop display (no NapCat injection).

        Use this when you want the normal QQ window for QR-code login.
        After logging in and closing QQ, start NapCat via ``start()`` /
        ``lingxuan run`` so it can quick-login with the cached session.
        """
        if self.is_running():
            raise RuntimeError(
                "NapCat/QQ 已在运行中 (PID {})。请先执行: lingxuan napcat stop".format(
                    self._read_pid()
                )
            )

        display = self._get_config_str("DISPLAY") or os.environ.get("DISPLAY", "")
        if not display:
            raise RuntimeError(
                "未设置 DISPLAY，无法打开桌面 QQ 窗口。请在图形桌面终端中运行。"
            )

        qq_bin = self._find_qq_binary()
        if qq_bin is None:
            raise RuntimeError("找不到 QQ 可执行文件。请确认 LinuxQQ 已安装。")

        env = os.environ.copy()
        env["DISPLAY"] = display
        # Ensure NapCat is NOT injected for GUI login.
        env.pop("LD_PRELOAD", None)
        env.pop("NAPCAT_BOOTMAIN", None)
        env.pop("NAPCAT_WORKDIR", None)

        qq_args = [str(qq_bin)]
        if self._get_config_bool("NAPCAT_NO_SANDBOX", default=False):
            qq_args.append("--no-sandbox")

        print(f"→ 启动普通 QQ 窗口 (DISPLAY={display}，无 NapCat 注入)...")
        print("  注意：普通 QQ 扫码留下的登录态，多数情况下不能被 NapCat 快速登录复用。")
        print("  若 lingxuan run 仍提示扫码，请直接扫 NapCat 控制台/二维码图片。")
        print("  登录成功后关闭 QQ，再执行: lingxuan run")
        print()

        self._qq_proc = subprocess.Popen(qq_args, env=env)
        self._write_pid(self._qq_proc.pid)
        print(f"✓ QQ 已启动 (PID {self._qq_proc.pid})")
        print("  关闭 QQ 窗口或按 Ctrl+C 结束。")

    def schedule_open_qrcode(self, *, timeout_s: float = 90.0) -> None:
        """On desktop, open NapCat's QR image when it is (re)written.

        NapCat writes ``cache/qrcode.png`` when interactive login is needed.
        Opening it with the desktop image viewer is the practical alternative
        to a normal QQ login window under LD_PRELOAD.
        """
        import threading

        qr_path = self._napcat_dir / "cache" / "qrcode.png"
        use_xvfb = self._get_config_bool("NAPCAT_USE_XVFB", default=True)
        if use_xvfb:
            return

        display = self._display or os.environ.get("DISPLAY", "")
        if not display:
            return

        def _worker() -> None:
            deadline = time.time() + timeout_s
            baseline = qr_path.stat().st_mtime if qr_path.exists() else 0.0
            while time.time() < deadline:
                time.sleep(1.0)
                if not qr_path.exists():
                    continue
                try:
                    mtime = qr_path.stat().st_mtime
                except OSError:
                    continue
                if mtime <= baseline:
                    continue
                env = os.environ.copy()
                env["DISPLAY"] = display
                try:
                    subprocess.Popen(
                        ["xdg-open", str(qr_path)],
                        env=env,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                    )
                    print(f"→ 已打开登录二维码图片: {qr_path}")
                except OSError as exc:
                    print(f"→ 无法自动打开二维码图片 ({exc})，请手动打开: {qr_path}")
                return

        threading.Thread(target=_worker, daemon=True).start()

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

        # Ensure all onebot11_*.json configs have the reverse WS client.
        # NapCat creates onebot11_<QQ>.json on first login with empty WS config,
        # which breaks the connection.  We patch them before every start.
        self._ensure_onebot11_configs()

        env = os.environ.copy()
        env["DISPLAY"] = self._display
        env["LD_PRELOAD"] = str(launcher_so.resolve())
        env["NAPCAT_WORKDIR"] = str(self._napcat_dir.resolve())

        # Electron utility workers often do not inherit QQ's ``-q`` argv.
        # Force single-process mode so NapCat can see ``-q`` / ``--qq``.
        # NAPCAT_QUICK_ACCOUNT alone only works when WebUI is enabled.
        env.setdefault("NAPCAT_DISABLE_MULTI_PROCESS", "1")

        # Auto-login: determine the QQ account for quick-login.
        # Priority: NAPCAT_QUICK_ACCOUNT env var > autoLoginAccount from webui.json.
        # Requires a prior QR-code scan on this machine to cache the session.
        # Note: with WebUI disabled, env-based quick login is a no-op in NapCat;
        # the ``-q`` CLI flag (below) is the reliable path.
        auto_account = self._get_config_str("NAPCAT_QUICK_ACCOUNT").strip()
        if not auto_account:
            auto_account = self._read_auto_login_account()
        if auto_account:
            env["NAPCAT_QUICK_ACCOUNT"] = auto_account

        # NAPCAT_BOOTMAIN tells loadNapCat.js where to find napcat.mjs.
        # Without this, it resolves ./napcat/napcat.mjs relative to the
        # project root (where the top-level loadNapCat.js lives), which
        # doesn't exist.  The actual NapCat shell lives in NapCat.Shell/.
        # Must use resolve() to get an absolute path — Node's path.join
        # treats relative paths as relative to CWD, not the env var value.
        napcat_shell = self._napcat_dir / "NapCat.Shell"
        if napcat_shell.is_dir():
            env["NAPCAT_BOOTMAIN"] = str(napcat_shell.resolve())

        # Build command line: QQ [--no-sandbox] [-q <account>] for quick-login.
        # --no-sandbox is needed in Docker / headless Chromium sandboxes;
        # desktop environments can set NAPCAT_NO_SANDBOX=false.
        qq_args = [str(qq_bin)]
        if self._get_config_bool("NAPCAT_NO_SANDBOX", default=False):
            qq_args.append("--no-sandbox")
        if auto_account:
            qq_args.extend(["-q", auto_account])

        if foreground:
            self._qq_proc = subprocess.Popen(
                qq_args,
                env=env,
            )
        else:
            log_path = self._logs_dir / f"napcat-{int(time.time())}.log"
            log_file = open(log_path, "w", encoding="utf-8")
            self._qq_proc = subprocess.Popen(
                qq_args,
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

    def _needs_ws_repair(self) -> bool:
        """Return True if any QQ-specific onebot11 config lacks the lingxuan WS client."""
        import json

        config_dir = self._napcat_dir / "config"
        if not config_dir.is_dir():
            return False

        for config_file in config_dir.glob("onebot11_*.json"):
            if config_file.name == "onebot11_.json":
                continue
            try:
                data = json.loads(config_file.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue
            clients = data.get("network", {}).get("websocketClients", [])
            if not any(c.get("name") == "lingxuan" for c in clients):
                return True
        return False

    def schedule_post_login_config_repair(self) -> None:
        """Restart NapCat after first login if QQ-specific config dropped the WS client.

        NapCat may create ``onebot11_<QQ>.json`` with an empty ``websocketClients``
        array *after* startup.  We poll for a short window, patch the config, and
        restart NapCat once so it reconnects to lingxuan.
        """
        import threading
        import time

        def _worker() -> None:
            for _ in range(18):  # up to 3 minutes
                time.sleep(10)
                if not self.is_running():
                    continue
                if not self._needs_ws_repair():
                    continue
                self._ensure_onebot11_configs()
                if self._needs_ws_repair():
                    continue
                print("→ NapCat 登录后已补全 OneBot 配置，重启 NapCat...")
                self.stop()
                time.sleep(2)
                self.start(foreground=False)
                return

        threading.Thread(target=_worker, daemon=True).start()

    def _ensure_onebot11_configs(self) -> None:
        """Patch all onebot11_*.json files to include the reverse WS client.

        NapCat creates ``onebot11_<QQ>.json`` on first login with an empty
        ``websocketClients`` array.  Without the WS client config, NapCat
        won't connect back to lingxuan.  We re-inject the config on every
        start to keep it in sync.

        Also patches ``webui.json`` to set ``autoLoginAccount`` when
        ``NAPCAT_QUICK_ACCOUNT`` is configured, so NapCat can auto-login
        via the WebUI quick function even when WebUI itself is disabled.
        """
        import json

        from lingxuan.napcat.config import generate_onebot11_config

        config_dir = self._napcat_dir / "config"
        if not config_dir.is_dir():
            return

        ws_url = self._get_config_str("NAPCAT_WS_URL", "ws://127.0.0.1:8080/onebot/v11/ws")
        ws_config = generate_onebot11_config(ws_url)

        for config_file in config_dir.glob("onebot11_*.json"):
            try:
                existing = json.loads(config_file.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue

            # Check if the WS client is already configured
            clients = existing.get("network", {}).get("websocketClients", [])
            has_lingxuan = any(c.get("name") == "lingxuan" for c in clients)

            if not has_lingxuan:
                existing["network"]["websocketClients"] = ws_config["network"]["websocketClients"]
                config_file.write_text(
                    json.dumps(existing, indent=2, ensure_ascii=False),
                    encoding="utf-8",
                )

        # Also patch webui.json to set autoLoginAccount for quick-login
        auto_account = self._get_config_str("NAPCAT_QUICK_ACCOUNT").strip()
        if auto_account:
            webui_path = config_dir / "webui.json"
            if webui_path.exists():
                try:
                    webui = json.loads(webui_path.read_text(encoding="utf-8"))
                except (json.JSONDecodeError, OSError):
                    return
                if webui.get("autoLoginAccount", "") != auto_account:
                    webui["autoLoginAccount"] = auto_account
                    webui_path.write_text(
                        json.dumps(webui, indent=2, ensure_ascii=False),
                        encoding="utf-8",
                    )

    def _read_auto_login_account(self) -> str:
        """Read autoLoginAccount from webui.json or NAPCAT_QUICK_ACCOUNT env."""
        import json

        # Env var is highest priority
        env_val = self._get_config_str("NAPCAT_QUICK_ACCOUNT").strip()
        if env_val:
            return env_val

        # Read from webui.json
        webui_path = self._napcat_dir / "config" / "webui.json"
        if webui_path.exists():
            try:
                webui = json.loads(webui_path.read_text(encoding="utf-8"))
                return webui.get("autoLoginAccount", "").strip()
            except (json.JSONDecodeError, OSError):
                pass

        return ""

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

    return NapCatManager(napcat_dir=napcat_dir, qq_dir=qq_dir, config=config)
