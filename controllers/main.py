# -*- coding: utf-8 -*-
import io
import json
import zipfile
from pathlib import Path

from odoo import http
from odoo.http import request, Response

_PROXY_FILES = {"proxy.py", "requirements.txt", "install.bat"}


class RedsysController(http.Controller):

    @http.route("/pos/redsys/pay", type="jsonrpc", auth="user")
    def pay(self, payment_method_id: int, amount: float, invoice_ref: str):
        return request.env["pos.payment.method"].redsys_send_payment(
            payment_method_id, amount, invoice_ref
        )

    @http.route("/pos/redsys/cancel", type="jsonrpc", auth="user")
    def cancel(self, payment_method_id: int):
        return request.env["pos.payment.method"].redsys_cancel_payment(payment_method_id)

    @http.route("/pos/redsys/download_proxy", type="http", auth="user")
    def download_proxy(self, payment_method_id=None):
        proxy_dir = Path(__file__).parent.parent / "static" / "proxy"

        # Build config.json from payment method if available, else use template
        method = None
        if payment_method_id:
            method = request.env["pos.payment.method"].browse(int(payment_method_id))

        if method and method.exists():
            config = {
                "comercio": method.redsys_comercio or "",
                "terminal": method.redsys_terminal or "1",
                "com_port": method.redsys_com_port or "COM9:,19200,N,8,1",
                "timeout": method.redsys_timeout or 45,
                "proxy_port": 8765,
                "redsys_usuario": method.redsys_usuario or "",
                "redsys_password": method.redsys_password or "",
                "mgmt_ws_url": "ws://localhost:10200",
            }
        else:
            config = {
                "comercio": "",
                "terminal": "1",
                "com_port": "COM9:,19200,N,8,1",
                "timeout": 45,
                "proxy_port": 8765,
                "redsys_usuario": "",
                "redsys_password": "",
                "mgmt_ws_url": "ws://localhost:10200",
            }

        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for file_path in proxy_dir.iterdir():
                if file_path.name in _PROXY_FILES:
                    zf.write(file_path, file_path.name)
            zf.writestr("config.json", json.dumps(config, indent=4, ensure_ascii=False))

        buf.seek(0)
        return Response(
            buf.read(),
            headers={
                "Content-Type": "application/zip",
                "Content-Disposition": "attachment; filename=redsys_proxy_windows.zip",
            },
        )
