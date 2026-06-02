# -*- coding: utf-8 -*-
import io
import json
import zipfile
from pathlib import Path

from odoo import http
from odoo.http import request, Response


class RedsysController(http.Controller):

    @http.route("/pos/redsys/pay", type="json", auth="user")
    def pay(self, payment_method_id: int, amount: float, invoice_ref: str):
        return request.env["pos.payment.method"].redsys_send_payment(
            payment_method_id, amount, invoice_ref
        )

    @http.route("/pos/redsys/cancel", type="json", auth="user")
    def cancel(self, payment_method_id: int):
        return request.env["pos.payment.method"].redsys_cancel_payment(payment_method_id)

    @http.route("/pos/redsys/download_proxy", type="http", auth="user")
    def download_proxy(self):
        proxy_dir = Path(__file__).parent.parent / "static" / "proxy"

        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for file_path in proxy_dir.iterdir():
                zf.write(file_path, file_path.name)

        buf.seek(0)
        return Response(
            buf.read(),
            headers={
                "Content-Type": "application/zip",
                "Content-Disposition": "attachment; filename=redsys_proxy_windows.zip",
            },
        )
