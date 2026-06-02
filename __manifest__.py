# -*- coding: utf-8 -*-
{
    "name": "POS Payment Redsys TPV-PC",
    "version": "19.0.1.0.0",
    "category": "Sales/Point of Sale",
    "summary": "Integración datáfono Redsys TPV-PC (Verifone P400) con Odoo POS",
    "description": """
        Integra el datáfono Redsys TPV-PC (Verifone P400 y compatibles) con Odoo POS.

        Arquitectura:
        - Proxy Windows (descargable desde este módulo) corre en el PC del kiosko
        - Proxy se comunica con TpvpcWinService vía WebSocket/HTTP en localhost
        - Odoo POS llama al proxy vía HTTP para iniciar cobros

        Requisitos:
        - Windows 10+ en el PC del kiosko
        - TpvpcWinService instalado (Redsys / CaixaBank)
        - Datáfono Verifone P400 (u otro compatible Redsys) conectado por USB
    """,
    "author": "Juan Cositt",
    "license": "LGPL-3",
    "depends": ["point_of_sale"],
    "assets": {
        "point_of_sale._assets_pos": [
            "pos_payment_redsys_tpvpc/static/src/js/payment_redsys.js",
        ],
    },
    "data": [
        "security/ir.model.access.csv",
        "views/pos_payment_method_views.xml",
    ],
    "external_dependencies": {
        "python": ["requests"],
    },
    "installable": True,
    "application": False,
    "auto_install": False,
}
