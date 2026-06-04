"""
Redsys TPV-PC Proxy
====================
Corre en el PC Windows del kiosko. Expone HTTP en :8765
y gestiona la comunicacion con TpvpcWinService (ws://localhost:10200)
y TpvpcPinpad (http://localhost:9000 + ws://localhost:9000).

Uso:
    python proxy.py

Requiere: pip install -r requirements.txt
"""
import asyncio
import hashlib
import json
import logging
import re
import subprocess
import time
from pathlib import Path
from typing import Optional

import httpx
import uvicorn
import websockets
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("C:/RedsysProxy/proxy.log", encoding="utf-8"),
    ],
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

CONFIG_PATH = Path(__file__).parent / "config.json"

def load_config() -> dict:
    if CONFIG_PATH.exists():
        return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    return {}

cfg = load_config()

MGMT_WS_URL = cfg.get("mgmt_ws_url", "ws://localhost:10200")
PINPAD_HTTP_URL = cfg.get("pinpad_http_url", "http://localhost:9000")
PINPAD_WS_URL = cfg.get("pinpad_ws_url", "ws://localhost:9000")
WSDL_URL = "https://tpvpc.redsys.es/TPV_PC/services/SerClsWSPasarelaPINPAD/wsdl/SerClsWSPasarelaPINPAD.wsdl"
PROXY_PORT = cfg.get("proxy_port", 8765)

# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------

_initialized: bool = False
_pinpad_ws: Optional[websockets.WebSocketClientProtocol] = None
_mgmt_ws: Optional[websockets.WebSocketClientProtocol] = None
_pending_future: Optional[asyncio.Future] = None
_lock = asyncio.Lock()

# Cached Redsys session — avoid repeated logins that trigger account lockout
_cached_jsessionid: Optional[str] = None
_jsession_expiry: float = 0.0
SESSION_TTL_SECONDS: int = cfg.get("session_ttl_seconds", 1800)  # 30 min default

# ---------------------------------------------------------------------------
# PinPad HTTP helpers
# ---------------------------------------------------------------------------

async def _pinpad_post(payload: dict) -> dict:
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(PINPAD_HTTP_URL, json=payload)
        resp.raise_for_status()
        data = resp.json()
        logger.debug(f"pinpad HTTP <- {payload.get('Command')} -> {data}")
        return data

async def _set_prop(name: str, value: str) -> None:
    await _pinpad_post({"Type": "set", "Command": name, "Args": [value]})

async def _set_int_prop(name: str, value: int) -> None:
    await _pinpad_post({"Type": "setInt", "Command": name, "Args": [value]})

async def _exec_func(name: str, args: list) -> dict:
    return await _pinpad_post({"Type": "func", "Command": name, "Args": args})

async def _get_prop(name: str) -> str:
    result = await _pinpad_post({"Type": "get", "Command": name})
    return result.get("Response", "")

# ---------------------------------------------------------------------------
# Redsys login — obtiene jsessionid fresco via POST directo a tpvpc.redsys.es
# ---------------------------------------------------------------------------

SESSION_VALIDATE_URL = "https://tpvpc.redsys.es/TPV_PC/validaSesion"


def _f5_cookie_value(f5_p: str, latency_ms: int = 500) -> str:
    def set_char_at(s: str, index: int, c: str) -> str:
        if index > len(s) - 1:
            return s
        return s[:index] + c + s[index + 1:]

    def set_byte(s: str, i: int, b: int) -> str:
        seg = (i // 16) * 32
        i = i & 15
        s = set_char_at(s, i + 16 + seg, chr((b >> 4) + 65))
        s = set_char_at(s, i + seg, chr((b & 15) + 65))
        return s

    latency = latency_ms & 0xFFFF
    s = f5_p
    s = set_byte(s, 40, latency >> 8)
    s = set_byte(s, 41, latency & 0xFF)
    s = set_byte(s, 35, 2)
    return s


async def get_fresh_jsessionid(usuario: str, password: str) -> str:
    login_base = "https://canales.redsys.es"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "es-ES,es;q=0.9",
    }
    async with httpx.AsyncClient(follow_redirects=True, timeout=20, headers=headers) as client:
        # STEP 1: Login canales.redsys.es
        r = await client.get(f"{login_base}/canales/login",
                             params={"Ico_Idioma": "1", "Ico_Prefijo": "2100",
                                     "Ico_PrefijoLogo": "2100", "Ico_Opcion": "101"})
        tag_m = re.search(r'<[Ii][Nn][Pp][Uu][Tt][^>]+name="Ico_Desafio"[^>]*>', r.text)
        desafio = ""
        if tag_m:
            val_m = re.search(r'value="([^"]*)"', tag_m.group(0))
            desafio = val_m.group(1) if val_m else ""
        logger.info(f"canales login: {r.status_code}, Ico_Desafio: '{desafio}'")

        f5_m = re.search(r"f5_p:'([A-Z]+)'", r.text)
        f5_name_m = re.search(r"cookie='(f5avr[^=]+)=", r.text)
        if f5_m and f5_name_m:
            f5_val = _f5_cookie_value(f5_m.group(1), latency_ms=523)
            client.cookies.set(f5_name_m.group(1), f5_val, domain="canales.redsys.es")

        r2 = await client.post(
            f"{login_base}/canales/login",
            headers={"Referer": str(r.url)},
            data={
                "Ico_Idioma": "1", "Ico_Prefijo": "2100", "Ico_PrefijoLogo": "2100",
                "Ico_Opcion": "103", "Ico_Usuario": usuario, "Ico_Password": password,
                "Ico_Certificado": "", "Ico_Desafio": desafio, "isIframe": "true",
            },
        )
        logger.info(f"canales login POST: {r2.status_code}, URL: {r2.url}")

        # STEP 2: presentaMenu con sesión de canales → JSESSIONID de tpvpc
        canales_jsid = None
        for raw_c in client.cookies.jar:
            if raw_c.name == "JSESSIONID" and "canales" in str(raw_c.domain):
                canales_jsid = raw_c.value
                logger.info(f"canales JSESSIONID: {canales_jsid[:20]}")

        async with httpx.AsyncClient(follow_redirects=True, timeout=15, headers=headers) as tc:
            if canales_jsid:
                tc.cookies.set("JSESSIONID", canales_jsid, domain="tpvpc.redsys.es")
            rt = await tc.get("https://tpvpc.redsys.es/TPV_PC/presentaMenu")
            logger.info(f"presentaMenu: {rt.status_code}, URL: {rt.url}")
            tpvpc_jsid = None
            for sc in rt.headers.get_list("set-cookie"):
                if "JSESSIONID" in sc:
                    m_sc = re.search(r"JSESSIONID=([^;,\s]+)", sc)
                    if m_sc:
                        tpvpc_jsid = m_sc.group(1)
                        logger.info(f"tpvpc JSESSIONID (presentaMenu): {tpvpc_jsid[:30]}")
            if not tpvpc_jsid:
                m_url = re.search(r"jsessionid=([^;?&\"'\s]+)", str(rt.url))
                if m_url:
                    tpvpc_jsid = m_url.group(1)
            if tpvpc_jsid:
                return tpvpc_jsid

        raise RuntimeError("No JSESSIONID from presentaMenu")

# ---------------------------------------------------------------------------
# Initialization
# ---------------------------------------------------------------------------

async def initialize(comercio: str, terminal: str, com_port: str, timeout: int) -> None:
    global _initialized, _pinpad_ws, _mgmt_ws

    logger.info("Iniciando conexion con TpvpcWinService...")

    # Cerrar conexiones previas para que TpvpcWinService mate TpvpcPinpad y libere COM port
    if _pinpad_ws:
        try:
            await _pinpad_ws.close()
        except Exception:
            pass
    if _mgmt_ws:
        try:
            await _mgmt_ws.close()
        except Exception:
            pass
    await asyncio.sleep(2)

    # STEP 1: Management WS
    _mgmt_ws = await websockets.connect(MGMT_WS_URL, open_timeout=5)
    client_id = str(int(time.time() * 1000))
    await _mgmt_ws.send(json.dumps({"ClientId": client_id, "Command": "Init", "Args": ["0"]}))
    raw = await asyncio.wait_for(_mgmt_ws.recv(), timeout=5)
    response = json.loads(raw)
    logger.info(f"Management response: {response}")

    pinpad_address = response.get("DataResponse", {}).get("PinPadAddress", "localhost:9000")
    pinpad_ws_url = f"ws://{pinpad_address}/"
    pinpad_http_url = f"http://{pinpad_address}"
    global PINPAD_HTTP_URL
    PINPAD_HTTP_URL = pinpad_http_url
    logger.info(f"PinPad address: {pinpad_address}")

    # STEP 2: Auth — reuse cached jsessionid if still valid to avoid lockouts
    global _cached_jsessionid, _jsession_expiry
    usuario = cfg.get("redsys_usuario", "")
    password = cfg.get("redsys_password", "")
    jsessionid = None
    if usuario and password:
        now = time.time()
        if _cached_jsessionid and now < _jsession_expiry:
            jsessionid = _cached_jsessionid
            remaining = int(_jsession_expiry - now)
            logger.info(f"Reutilizando jsessionid cacheado (expira en {remaining}s): {jsessionid[:30]}")
        else:
            try:
                jsessionid = await get_fresh_jsessionid(usuario, password)
                _cached_jsessionid = jsessionid
                _jsession_expiry = time.time() + SESSION_TTL_SECONDS
                logger.info(f"Nuevo jsessionid obtenido (TTL {SESSION_TTL_SECONDS}s): {jsessionid[:30]}")
            except Exception as e:
                logger.warning(f"Auth failed: {e} — continuando sin sesion")

    # STEP 3: Conectar WS :9000 ANTES de HTTP calls (secuencia exacta del browser)
    logger.info(f"Conectando WebSocket eventos pinpad {pinpad_ws_url}")
    _pinpad_ws = await websockets.connect(pinpad_ws_url, open_timeout=5)

    # STEP 4: Secuencia HTTP exacta del browser (HAR verificado)
    logger.info("SetProp Usuario")
    await _set_prop("Usuario", usuario)
    await asyncio.sleep(0.5)
    logger.info("SetIntProp TimeOut")
    await _set_int_prop("TimeOut", cfg.get("timeout", 45))
    logger.info("SetProp Version 8.1")
    await _set_prop("Version", "8.1")

    if jsessionid:
        session_url = f"{SESSION_VALIDATE_URL};jsessionid={jsessionid}"
        logger.info(f"EstConfSesion: {session_url[:70]}")
        result = await _exec_func("EstConfSesion", [session_url, jsessionid])
        logger.info(f"EstConfSesion result: {result}")

    logger.info(f"IniciaComunicacion -> {pinpad_http_url}")
    result = await _exec_func("IniciaComunicacion", [comercio, terminal, WSDL_URL, "PAGO", com_port])
    logger.info(f"IniciaComunicacion result: {result}")

    # STEP 5: Esperar pinpadIE_Inicializado en WS :9000
    deadline = asyncio.get_event_loop().time() + 20
    while True:
        remaining = deadline - asyncio.get_event_loop().time()
        if remaining <= 0:
            raise RuntimeError("Timeout esperando pinpadIE_Inicializado")
        init_event = await asyncio.wait_for(_pinpad_ws.recv(), timeout=remaining)
        logger.info(f"Pinpad init event: {init_event}")
        if init_event == "pinpadIE_Inicializado":
            break
        if init_event in ("pinpadIE_SesionInvalida", "pinpadIE__IPinPadIEEvents_Event_Error"):
            raise RuntimeError(f"{init_event}")

    asyncio.create_task(_event_listener())
    _initialized = True
    logger.info("Datafono listo")


async def _event_listener() -> None:
    global _pending_future, _initialized, _pinpad_ws
    try:
        async for message in _pinpad_ws:
            logger.info(f"Terminal event: {message}")
            if _pending_future and not _pending_future.done():
                if message == "pinpadIE_FinTransaccion":
                    _pending_future.set_result({"status": "done"})
                elif message in ("pinpadIE_EsperandoTarjeta", "pinpadIE_LecturaOK"):
                    pass  # wait for FinTransaccion
                elif message == "pinpadIE_ErrorTransaccion":
                    _pending_future.set_result({"status": "error", "event": message})
                elif message == "pinpadIE_CanceladaOK":
                    _pending_future.set_result({"status": "cancelled"})
                elif message == "pinpadIE_SesionInvalida":
                    _initialized = False
                    _cached_jsessionid = None
                    _jsession_expiry = 0.0
                    logger.warning("SesionInvalida — cache de jsessionid borrado, forzara re-auth")
                    _pending_future.set_result({"status": "error", "event": "session_invalid"})
    except websockets.exceptions.ConnectionClosed:
        logger.warning("WebSocket cerrado - marcando como no inicializado")
        _initialized = False
        _pinpad_ws = None
        if _mgmt_ws:
            await _mgmt_ws.close()

# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------

app = FastAPI(title="Redsys TPV-PC Proxy", version="1.0.0")


class PayRequest(BaseModel):
    amount: float
    invoice: str
    comercio: str = ""
    terminal: str = "1"
    timeout: int = 60
    com_port: str = "COM9:,19200,N,8,1"


@app.get("/health")
async def health():
    session_ttl_remaining = max(0, int(_jsession_expiry - time.time())) if _cached_jsessionid else 0
    return {
        "status": "ok",
        "initialized": _initialized,
        "session_cached": _cached_jsessionid is not None,
        "session_ttl_remaining_s": session_ttl_remaining,
    }


@app.post("/pay")
async def pay(req: PayRequest):
    global _pending_future

    async with _lock:
        comercio = req.comercio or cfg.get("comercio", "")
        terminal = req.terminal or cfg.get("terminal", "1")
        com_port = req.com_port or cfg.get("com_port", "COM9:,19200,N,8,1")

        if not _initialized:
            try:
                await initialize(comercio, terminal, com_port, req.timeout)
            except Exception as e:
                raise HTTPException(status_code=503, detail=f"No se pudo inicializar el datafono: {type(e).__name__}: {e}")

        # Set payment properties (order and values match browser HAR)
        await _set_prop("OpcionesPagoCaixa", "N")
        await _set_prop("Importe", str(req.amount))
        await _set_prop("Moneda", "978")  # EUR
        await _set_prop("Factura", req.invoice[:12])
        await _set_prop("TipoOperacion", "PAGO")
        await _set_prop("OpcionesPago", "S")

        # Create future for async event
        loop = asyncio.get_event_loop()
        _pending_future = loop.create_future()

        # Start card read
        await _exec_func("LeeTarjeta", [])
        logger.info(f"LeeTarjeta enviado - importe: {req.amount}€ factura: {req.invoice}")

    # Wait for terminal response (outside lock)
    try:
        result = await asyncio.wait_for(_pending_future, timeout=req.timeout)
    except asyncio.TimeoutError:
        _pending_future = None
        return {"authorized": False, "error": "Timeout - el cliente no respondio"}

    _pending_future = None

    if result["status"] == "done":
        receipt = await _get_prop("DatosRecibo")
        return {"authorized": True, "receipt": receipt}
    elif result["status"] == "cancelled":
        return {"authorized": False, "error": "Operacion cancelada"}
    else:
        return {"authorized": False, "error": f"Error en datafono: {result.get('event', 'unknown')}"}


@app.get("/debug")
async def debug():
    results = {}

    # COM ports via mode command
    try:
        out = subprocess.check_output("mode", shell=True, text=True, timeout=5, stderr=subprocess.STDOUT)
        results["com_ports"] = out
    except Exception as e:
        results["com_ports"] = f"ERROR: {e}"

    # Tpvpc services
    try:
        out = subprocess.check_output(
            'sc query type= all state= all | findstr /i "tpvpc\\|pinpad\\|redsys"',
            shell=True, text=True, timeout=5, stderr=subprocess.STDOUT
        )
        results["services"] = out
    except Exception as e:
        results["services"] = f"ERROR: {e}"

    # Port 9000 listening
    try:
        out = subprocess.check_output(
            "netstat -an | findstr :9000",
            shell=True, text=True, timeout=5, stderr=subprocess.STDOUT
        )
        results["port_9000"] = out
    except Exception as e:
        results["port_9000"] = f"ERROR (probablemente no escucha): {e}"

    # Port 10200 listening
    try:
        out = subprocess.check_output(
            "netstat -an | findstr :10200",
            shell=True, text=True, timeout=5, stderr=subprocess.STDOUT
        )
        results["port_10200"] = out
    except Exception as e:
        results["port_10200"] = f"ERROR (probablemente no escucha): {e}"

    # Pinpad HTTP ping
    try:
        async with httpx.AsyncClient(timeout=3) as client:
            resp = await client.get(PINPAD_HTTP_URL)
            results["pinpad_http"] = f"HTTP {resp.status_code}: {resp.text[:200]}"
    except Exception as e:
        results["pinpad_http"] = f"ERROR: {type(e).__name__}: {e}"

    # Config
    results["config"] = cfg
    results["initialized"] = _initialized

    return results


@app.post("/cancel")
async def cancel():
    global _pending_future
    try:
        await _exec_func("ParaLectura", [])
        if _pending_future and not _pending_future.done():
            _pending_future.set_result({"status": "cancelled"})
        return {"cancelled": True}
    except Exception as e:
        return {"cancelled": False, "error": str(e)}


@app.post("/reinit")
async def reinit(comercio: str = "", terminal: str = "1", com_port: str = "COM9:,19200,N,8,1"):
    global _initialized
    _initialized = False
    try:
        await initialize(
            comercio or cfg.get("comercio", ""),
            terminal,
            com_port,
            cfg.get("timeout", 45),
        )
        return {"ok": True}
    except Exception as e:
        raise HTTPException(status_code=503, detail=str(e))


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=PROXY_PORT, log_level="info")
