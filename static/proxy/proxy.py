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
import json
import logging
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
SESSION_URL = "https://canales.redsys.es/canales/lacaixa/TPV_PC/validaSesion"
JSESSIONID_FALLBACK = "0000aZfEEzH_IKInuzKzy605-D6:1be05rmv7"
PROXY_PORT = cfg.get("proxy_port", 8765)

# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------

_initialized: bool = False
_pinpad_ws: Optional[websockets.WebSocketClientProtocol] = None
_pending_future: Optional[asyncio.Future] = None
_lock = asyncio.Lock()

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
# Initialization
# ---------------------------------------------------------------------------

async def initialize(comercio: str, terminal: str, com_port: str, timeout: int) -> None:
    global _initialized, _pinpad_ws

    logger.info("Iniciando conexion con TpvpcWinService...")

    # Connect to management WebSocket to get pinpad address
    try:
        async with websockets.connect(MGMT_WS_URL, open_timeout=5) as mgmt_ws:
            client_id = str(int(time.time() * 1000))
            await mgmt_ws.send(json.dumps({
                "ClientId": client_id,
                "Command": "Init",
                "Args": ["0"],
            }))
            raw = await asyncio.wait_for(mgmt_ws.recv(), timeout=5)
            response = json.loads(raw)
            logger.info(f"Management response: {response}")
    except Exception as e:
        logger.warning(f"Management service error: {e} - continuando con defaults")

    # Initialize pinpad via HTTP
    usuario = comercio + "2100"
    await _set_prop("Usuario", usuario)
    await _set_int_prop("TimeOut", timeout)
    await _set_prop("Version", "8.1")

    # EstConfSesion - try with fallback session
    try:
        await _exec_func("EstConfSesion", [SESSION_URL, JSESSIONID_FALLBACK])
    except Exception as e:
        logger.warning(f"EstConfSesion failed: {e} - continuando sin sesion")

    # IniciaComunicacion
    await _exec_func("IniciaComunicacion", [comercio, terminal, WSDL_URL, "PAGO", com_port])
    logger.info("IniciaComunicacion enviado")

    # Connect WebSocket for async events
    _pinpad_ws = await websockets.connect(PINPAD_WS_URL, open_timeout=5)
    asyncio.create_task(_event_listener())

    _initialized = True
    logger.info("Datafono inicializado correctamente")


async def _event_listener() -> None:
    global _pending_future, _initialized, _pinpad_ws
    try:
        async for message in _pinpad_ws:
            logger.info(f"Terminal event: {message}")
            if _pending_future and not _pending_future.done():
                if message == "pinpadIE_FinTransaccion":
                    _pending_future.set_result({"status": "done"})
                elif message == "pinpadIE_LecturaOK":
                    pass  # wait for FinTransaccion
                elif message == "pinpadIE_ErrorTransaccion":
                    _pending_future.set_result({"status": "error", "event": message})
                elif message == "pinpadIE_CanceladaOK":
                    _pending_future.set_result({"status": "cancelled"})
                elif message == "pinpadIE_SesionInvalida":
                    _initialized = False
                    _pending_future.set_result({"status": "error", "event": "session_invalid"})
    except websockets.exceptions.ConnectionClosed:
        logger.warning("WebSocket cerrado - marcando como no inicializado")
        _initialized = False
        _pinpad_ws = None

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
    return {"status": "ok", "initialized": _initialized}


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
                raise HTTPException(status_code=503, detail=f"No se pudo inicializar el datafono: {e}")

        # Set payment properties
        cents = str(int(round(req.amount * 100))).zfill(12)
        await _set_prop("Importe", cents)
        await _set_prop("Moneda", "978")  # EUR
        await _set_prop("Factura", req.invoice[:12])
        await _set_prop("TipoOperacion", "PAGO")
        await _set_prop("OpcionesPago", "")

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
