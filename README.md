# POS Payment Redsys TPV-PC

Módulo Odoo 19 para integrar datáfonos Redsys (Verifone P400 y compatibles) con Odoo POS via TpvpcWinService.

## Requisitos

- Odoo 19 (Community o Enterprise)
- PC kiosko con Windows 10+
- TpvpcWinService instalado (`C:\Program Files (x86)\REDSYS\TpvpcWinService\`)
- Datáfono Verifone P400 conectado por USB al kiosko
- Python 3.10+ en el kiosko Windows

## Arquitectura

```
Odoo POS (servidor Linux)
    │ HTTP POST /pos/redsys/pay
    ▼
Proxy Python (kiosko Windows :8765)
    │ WebSocket ws://localhost:10200  (management)
    │ HTTP POST http://localhost:9000 (operaciones)
    │ WebSocket ws://localhost:9000   (eventos async)
    ▼
TpvpcWinService / TpvpcPinpad
    │ USB COM9
    ▼
Verifone P400
```

## Instalación paso a paso

### 1. Instalar el módulo en Odoo

1. Copiar `pos_payment_redsys_tpvpc/` a tu directorio de addons
2. Reiniciar Odoo con `--update=all` o actualizar lista de apps
3. Instalar el módulo desde **Apps → Punto de Venta**

### 2. Descargar e instalar el Proxy Windows

1. En Odoo: **Punto de Venta → Configuración → Métodos de Pago**
2. Crear o abrir un método de pago → marcar **"Habilitar Redsys TPV-PC"**
3. Click en **"Descargar Proxy Windows"** → descarga `redsys_proxy_windows.zip`
4. Copiar el ZIP al kiosko Windows y descomprimir (ej: `C:\RedsysProxy\`)
5. Abrir `config.json` y verificar/editar:

```json
{
    "comercio": "369559141",
    "terminal": "1",
    "com_port": "COM9:,19200,N,8,1",
    "timeout": 45,
    "proxy_port": 8765
}
```

> **Verificar COM port**: Abrir Device Manager → Puertos (COM y LPT) → buscar "Verifone" o "USB Serial". El número que aparece (ej: COM9) va en `com_port`.

6. Ejecutar `install.bat` **como Administrador**
7. Verificar que funciona abriendo en el navegador del kiosko:
   `http://localhost:8765/health` → debe responder `{"status": "ok"}`

### 3. Configurar el método de pago en Odoo

1. **Punto de Venta → Configuración → Métodos de Pago**
2. Crear método de pago nuevo (tipo: Banco/Tarjeta)
3. Marcar **"Habilitar Redsys TPV-PC"**
4. Configurar:
   - **URL Proxy Windows**: `http://<IP-kiosko>:8765`
     - Si Odoo corre en el mismo PC: `http://localhost:8765`
     - Si Odoo está en otro servidor: `http://10.0.1.XX:8765` (IP del kiosko)
   - **Código de Comercio**: el que te dio el banco (ej: `369559141`)
   - **Número de Terminal**: `1` (o el que corresponda)
   - **Timeout**: `60` segundos (recomendado)
5. Click **"Probar conexión"** → debe aparecer mensaje verde "Proxy OK"
6. Guardar

### 4. Añadir el método de pago al TPV

1. **Punto de Venta → Configuración → Tiendas/TPV**
2. Abrir la configuración del TPV correspondiente
3. En **Métodos de Pago**, añadir el método Redsys creado
4. Guardar y reabrir sesión POS

## Uso en caja

1. Abrir sesión POS normalmente
2. Añadir productos al carrito
3. Click **"Pago"** → seleccionar método Redsys
4. El datáfono se activa automáticamente esperando tarjeta
5. Cliente pasa tarjeta/NFC en el P400
6. El TPV confirma el cobro automáticamente

## Solución de problemas

### "Proxy no disponible"
- Verificar que `proxy.py` está corriendo en el kiosko: `http://localhost:8765/health`
- Si no responde, abrir cmd como admin y ejecutar: `venv\Scripts\python proxy.py`
- Verificar firewall Windows: permitir puerto 8765

### "No se pudo inicializar el datáfono"
- Verificar que TpvpcWinService está corriendo: `sc query TpvpcWinService`
- Si está parado: `sc start TpvpcWinService`
- Verificar que el USB del P400 está conectado al PC
- Revisar COM port en Device Manager y actualizar `config.json`

### "Timeout"
- Aumentar `timeout` en la configuración del método de pago (máx recomendado: 90s)
- Verificar que el datáfono no está bloqueado (reiniciar terminal)

### Reinicializar el datáfono manualmente
```
POST http://<IP-kiosko>:8765/reinit
```

## Ficheros del proxy

| Fichero | Descripción |
|---------|-------------|
| `proxy.py` | Servicio principal FastAPI |
| `config.json` | Configuración (comercio, terminal, COM port) |
| `requirements.txt` | Dependencias Python |
| `install.bat` | Instalador (crea venv + registro arranque automático) |

## Compatibilidad

| Terminal | Firmware | Estado |
|----------|----------|--------|
| Verifone P400 | Redsys / CaixaBank | ✅ Probado |
| Verifone VX820 | Redsys | ✅ Compatible (mismo protocolo) |
| Otros terminales Redsys TPV-PC | Redsys | ✅ Compatible |

Requiere TpvpcWinService v3000+ instalado por el banco adquirente.
