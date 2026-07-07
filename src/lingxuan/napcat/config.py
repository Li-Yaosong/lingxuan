"""NapCat configuration file generation.

Generates ``onebot11_*.json`` and ``webui.json`` for NapCat,
based on lingxuan's reverse WebSocket endpoint.
"""

from __future__ import annotations

import json
from pathlib import Path


def generate_onebot11_config(ws_url: str) -> dict:
    """Generate a NapCat onebot11 config dict with a reverse WebSocket client.

    The ``ws_url`` should point to lingxuan's OneBot v11 reverse WS endpoint,
    e.g. ``ws://127.0.0.1:8080/onebot/v11/ws``.
    """
    return {
        "network": {
            "httpServers": [],
            "httpSseServers": [],
            "httpClients": [],
            "websocketServers": [],
            "websocketClients": [
                {
                    "enable": True,
                    "name": "lingxuan",
                    "url": ws_url,
                    "reportSelfMessage": False,
                    "messagePostFormat": "array",
                    "token": "",
                    "debug": False,
                    "heartInterval": 30000,
                    "reconnectInterval": 30000,
                    "verifyCertificate": True,
                }
            ],
            "plugins": [],
        },
        "musicSignUrl": "",
        "enableLocalFile2Url": False,
        "parseMultMsg": False,
        "imageDownloadProxy": "",
        "timeout": {
            "baseTimeout": 10000,
            "uploadSpeedKBps": 256,
            "downloadSpeedKBps": 256,
            "maxTimeout": 1800000,
        },
    }


def generate_webui_config(
    *, disable: bool = True, auto_login_account: str = ""
) -> dict:
    """Generate a NapCat webui.json config dict.

    When ``disable=True``, the WebUI is turned off for headless operation.
    Login is via console QR code or auto-login instead.

    ``auto_login_account`` sets the QQ number for NapCat's quick-login
    on startup.  The QQ session must have been established by a prior
    QR-code scan on this machine.
    """
    return {
        "host": "::",
        "port": 6099,
        "token": "",
        "loginRate": 10,
        "autoLoginAccount": auto_login_account,
        "theme": {},
        "disableWebUI": disable,
        "accessControlMode": "none",
        "ipWhitelist": [],
        "ipBlacklist": [],
        "enableXForwardedFor": False,
        "enable2FA": False,
        "totpSecret": "",
    }


def write_configs(config_dir: Path, ws_url: str) -> list[Path]:
    """Write NapCat config files to ``config_dir``.

    Creates:
    - ``onebot11_.json`` — default reverse WS config (empty QQ suffix)
    - ``onebot11_<QQ>.json`` — updates any existing QQ-specific configs
    - ``webui.json`` — headless config (WebUI disabled)

    Returns list of written file paths.
    """
    config_dir.mkdir(parents=True, exist_ok=True)

    written: list[Path] = []

    # onebot11 config — use empty suffix as default template
    onebot11_path = config_dir / "onebot11_.json"
    onebot11_path.write_text(
        json.dumps(generate_onebot11_config(ws_url), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    written.append(onebot11_path)

    # Also write/update any existing QQ-specific onebot11_<QQ>.json files.
    # NapCat creates these on first login with empty WS config, which breaks
    # the reverse WebSocket connection to lingxuan.
    ws_config = generate_onebot11_config(ws_url)
    for existing in config_dir.glob("onebot11_*.json"):
        if existing.name == "onebot11_.json":
            continue
        existing.write_text(
            json.dumps(ws_config, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        written.append(existing)

    # webui config — disable WebUI for headless
    webui_path = config_dir / "webui.json"
    webui_path.write_text(
        json.dumps(generate_webui_config(disable=True), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    written.append(webui_path)

    return written
