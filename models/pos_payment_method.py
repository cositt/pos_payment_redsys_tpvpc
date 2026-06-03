# -*- coding: utf-8 -*-
import logging
import requests
from odoo import fields, models, api, _
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class PosPaymentMethod(models.Model):
    _inherit = "pos.payment.method"

    redsys_enabled = fields.Boolean(
        string="Habilitar Redsys TPV-PC",
        default=False,
    )
    redsys_proxy_url = fields.Char(
        string="URL Proxy Windows",
        default="http://10.0.1.21:8765",
        help="URL del proxy corriendo en el PC del kiosko (ej: http://10.0.1.21:8765)",
    )
    redsys_comercio = fields.Char(
        string="Código de Comercio",
        default="369559141",
        help="Código de comercio asignado por el banco",
    )
    redsys_terminal = fields.Char(
        string="Número de Terminal",
        default="1",
    )
    redsys_com_port = fields.Char(
        string="Puerto COM Datáfono",
        default="COM9:,19200,N,8,1",
        help="Puerto serie del datáfono (ej: COM9:,19200,N,8,1)",
    )
    redsys_usuario = fields.Char(
        string="Usuario Redsys",
        help="Usuario de acceso a canales.redsys.es (ej: 3695591412100)",
    )
    redsys_password = fields.Char(
        string="Password Redsys",
        help="Contraseña de acceso a canales.redsys.es",
    )
    redsys_timeout = fields.Integer(
        string="Timeout (seg)",
        default=60,
    )
    redsys_proxy_status = fields.Selection(
        selection=[
            ("unknown", "Sin comprobar"),
            ("ok", "Conectado"),
            ("error", "Error"),
        ],
        string="Estado Proxy",
        default="unknown",
        readonly=True,
    )

    def action_test_redsys_proxy(self):
        self.ensure_one()
        try:
            resp = requests.get(
                f"{self.redsys_proxy_url}/health",
                timeout=5,
            )
            if resp.status_code == 200:
                self.redsys_proxy_status = "ok"
                return {
                    "type": "ir.actions.client",
                    "tag": "display_notification",
                    "params": {
                        "title": _("Proxy OK"),
                        "message": resp.json().get("status", "ok"),
                        "type": "success",
                    },
                }
        except Exception as e:
            self.redsys_proxy_status = "error"
            raise UserError(_("No se pudo conectar al proxy: %s") % str(e))

    def action_download_redsys_proxy(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_url",
            "url": f"/pos/redsys/download_proxy?payment_method_id={self.id}",
            "target": "new",
        }

    def redsys_send_payment(self, payment_method_id: int, amount: float, invoice_ref: str) -> dict:
        method = self.browse(payment_method_id)
        try:
            resp = requests.post(
                f"{method.redsys_proxy_url}/pay",
                json={
                    "amount": amount,
                    "invoice": invoice_ref,
                    "comercio": method.redsys_comercio or "",
                    "terminal": method.redsys_terminal or "1",
                    "com_port": method.redsys_com_port or "COM9:,19200,N,8,1",
                    "timeout": method.redsys_timeout,
                },
                timeout=method.redsys_timeout + 10,
            )
            return resp.json()
        except requests.exceptions.ConnectionError:
            return {"authorized": False, "error": _("Proxy no disponible. Comprueba que el servicio está corriendo en el kiosko.")}
        except requests.exceptions.Timeout:
            return {"authorized": False, "error": _("Timeout esperando respuesta del datáfono.")}
        except Exception as e:
            _logger.exception("Error en pago Redsys")
            return {"authorized": False, "error": str(e)}

    def redsys_cancel_payment(self, payment_method_id: int) -> dict:
        method = self.browse(payment_method_id)
        try:
            resp = requests.post(
                f"{method.redsys_proxy_url}/cancel",
                timeout=5,
            )
            return resp.json()
        except Exception:
            return {"cancelled": False}
