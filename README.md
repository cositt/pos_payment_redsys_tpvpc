# POS Payment Redsys TPV-PC

Módulo Odoo 19 para integrar datáfonos Redsys (Verifone P400 y compatibles) con Odoo POS y kioscos self-order, via proxy Python + TpvpcWinService.

Compatible con **POS regular** (`/pos/ui`) y **kiosco self-order** (`/pos-self/`).

---

## Índice

1. [Arquitectura](#arquitectura)
2. [Requisitos](#requisitos)
3. [Instalación del módulo en Odoo](#1-instalación-del-módulo-en-odoo)
4. [Instalación del proxy en Windows](#2-instalación-del-proxy-en-windows)
5. [Tunnel Cloudflare permanente](#3-tunnel-cloudflare-permanente-recomendado)
6. [Configuración en Odoo](#4-configuración-en-odoo)
7. [Flujo de pago](#flujo-de-pago)
8. [Solución de problemas](#solución-de-problemas)
9. [Referencia técnica](#referencia-técnica)

---

## Arquitectura

### Con Cloudflare Tunnel (producción recomendada)

Necesaria cuando Odoo corre en un servidor remoto (internet) y el proxy/datáfono están en red local del kiosco.

```
Odoo Server (internet, ej: latrufa.srv.cositt.net)
    │
    │  HTTPS POST https://<tu-dominio>/pay
    ▼
Cloudflare Edge
    │
    │  Tunnel cifrado (cloudflared.exe como servicio Windows)
    ▼
Proxy Python (kiosco Windows, localhost:8765)
    │  WebSocket  ws://localhost:10200   (management TpvpcWinService)
    │  HTTP POST  http://localhost:9000  (operaciones pinpad)
    │  WebSocket  ws://localhost:9000    (eventos async pinpad)
    ▼
TpvpcWinService / TpvpcPinpad  (instalado por CaixaBank/Redsys)
    │  USB serie  COM9 (o el que corresponda)
    ▼
Verifone P400
```

### Sin tunnel (Odoo y kiosco en misma red local)

```
Odoo Server (LAN)
    │  HTTP POST http://10.0.1.XX:8765/pay
    ▼
Proxy Python (kiosco Windows :8765)
    ▼
TpvpcWinService → Verifone P400
```

---

## Requisitos

| Componente | Requisito |
|-----------|-----------|
| Odoo | 19.0 (Community o Enterprise), módulos `point_of_sale` + `pos_self_order` |
| PC Kiosco | Windows 10+ |
| TpvpcWinService | Instalado por CaixaBank/Redsys (`C:\Program Files (x86)\REDSYS\TpvpcWinService\`) |
| Datáfono | Verifone P400 (o compatible Redsys TPV-PC) conectado por USB |
| Python | 3.10+ en el kiosco Windows |
| Cloudflare | Dominio propio en Cloudflare (solo si Odoo está en servidor remoto) |

---

## 1. Instalación del módulo en Odoo

```bash
# Copiar el módulo al directorio de addons
cp -r pos_payment_redsys_tpvpc/ /ruta/custom_addons/

# Reiniciar Odoo con upgrade
odoo -u pos_payment_redsys_tpvpc -d <nombre_bd> --stop-after-init
```

O desde la interfaz: **Apps → Buscar "Redsys TPV-PC" → Instalar**

> El módulo requiere que `pos_self_order` esté instalado. Si usas solo POS regular sin kiosco, instala `pos_self_order` igualmente o elimina la dependencia del `__manifest__.py`.

---

## 2. Instalación del proxy en Windows

### 2.1 Descargar el proxy desde Odoo

1. **Punto de Venta → Configuración → Métodos de Pago**
2. Abrir el método de pago Redsys → click **"Descargar Proxy Windows"**
3. Se descarga `redsys_proxy_windows.zip` ya preconfigurado con los datos del método de pago

### 2.2 Instalar en el kiosco

1. Copiar el ZIP al kiosco Windows y descomprimir en `C:\RedsysProxy\`
2. Verificar `config.json`:

```json
{
    "comercio": "369559141",
    "terminal": "1",
    "com_port": "COM9:,19200,N,8,1",
    "timeout": 45,
    "proxy_port": 8765,
    "redsys_usuario": "TU_USUARIO_REDSYS",
    "redsys_password": "TU_PASSWORD_REDSYS",
    "mgmt_ws_url": "ws://localhost:10200"
}
```

> **`redsys_usuario` y `redsys_password`**: Son las credenciales de acceso a `canales.redsys.es` asignadas por CaixaBank/Redsys (formato típico: `3695591412100`). Imprescindibles para que el proxy autentique con TpvpcWinService.

> **COM port**: Device Manager → Puertos (COM y LPT) → buscar "Verifone" o "USB Serial". El número que aparece (ej: COM9) va en `com_port`.

3. Ejecutar `install.bat` **como Administrador** — crea el entorno virtual Python e instala el proxy como tarea programada de arranque automático.

4. Verificar que funciona:
```
GET http://localhost:8765/health
```
Respuesta esperada:
```json
{
    "status": "ok",
    "session_cached": true,
    "session_ttl_remaining_s": 1743
}
```

> **`session_cached: true`** indica que el proxy ya tiene sesión activa con Redsys y no hará login en cada pago — esto es importante para evitar bloqueos de cuenta.

### 2.3 Gestión del servicio proxy

```bat
REM Ver estado
schtasks /query /tn RedsysProxy

REM Arrancar manualmente
schtasks /run /tn RedsysProxy

REM Parar y reiniciar
taskkill /F /IM python.exe
schtasks /run /tn RedsysProxy

REM Ver logs
type C:\RedsysProxy\proxy.log
```

---

## 3. Tunnel Cloudflare permanente (recomendado)

Necesario cuando Odoo está en servidor remoto y no puede acceder directamente a `http://10.0.1.XX:8765`.

### 3.1 Crear el tunnel en Cloudflare

1. Ir a [Cloudflare Zero Trust](https://one.dash.cloudflare.com/) → **Networks → Tunnels**
2. Click **"Create a tunnel"** → tipo **Cloudflared**
3. Nombre: `redsys-proxy` (o el que prefieras)
4. En **"Install connector"** → Windows → copiar el comando de instalación con el token
5. **Importante**: Copiar el token que aparece en este paso (forma `eyJ...`). Este token es único y no se puede recuperar después — si se pierde hay que crear un nuevo tunnel.

### 3.2 Instalar cloudflared en el kiosco Windows

Ejecutar como Administrador en el kiosco:

```bat
REM Crear directorio
mkdir C:\cloudflared

REM Descargar cloudflared.exe (desde https://github.com/cloudflare/cloudflared/releases)
REM Copiarlo a C:\cloudflared\cloudflared.exe

REM Instalar como servicio Windows con el token del paso anterior
C:\cloudflared\cloudflared.exe service install <TOKEN_DEL_TUNNEL>

REM Verificar que el servicio está corriendo
sc query cloudflared
```

El estado debe mostrar `STATE: 4 RUNNING`.

### 3.3 Configurar la ruta del tunnel

De vuelta en Cloudflare Dashboard, en el wizard del tunnel:

- **Public hostname**: `redsys-proxy.tu-dominio.com`
- **Service**: `http://localhost:8765`

> **Importante**: Esta configuración se hace en el paso "Route tunnel" del wizard, NO en "Application Routes" (que requiere autenticación adicional).

### 3.4 Verificar el tunnel

Desde cualquier red externa:
```bash
curl https://redsys-proxy.tu-dominio.com/health
```

Debe responder igual que el localhost.

### 3.5 Actualizar la URL en Odoo

En el método de pago Redsys:
- **URL Proxy Windows**: `https://redsys-proxy.tu-dominio.com`
- Click **"Probar conexión"** → debe aparecer "Proxy OK"

---

## 4. Configuración en Odoo

### 4.1 Crear el método de pago

1. **Punto de Venta → Configuración → Métodos de Pago**
2. Crear nuevo método (tipo: Banco/Tarjeta)
3. Configurar campos:

| Campo | Valor | Descripción |
|-------|-------|-------------|
| Nombre | Tarjeta (Redsys) | Visible para el cliente |
| Habilitar Redsys TPV-PC | ✓ | Activa la integración |
| URL Proxy Windows | `https://redsys-proxy.tu-dominio.com` | URL del proxy (con tunnel) o `http://IP:8765` (red local) |
| Código de Comercio | `369559141` | Asignado por el banco |
| Número de Terminal | `1` | Número de terminal |
| Puerto COM Datáfono | `COM9:,19200,N,8,1` | Puerto serie + parámetros |
| Usuario Redsys | `3695591412100` | Usuario `canales.redsys.es` |
| Password Redsys | `**********` | Password `canales.redsys.es` |
| Timeout | `60` | Segundos máximos esperando tarjeta |

4. Al marcar "Habilitar Redsys TPV-PC", el campo `use_payment_terminal` se configura automáticamente a `redsys_tpvpc`.
5. Click **"Probar conexión"** → verificar estado verde.
6. Guardar.

### 4.2 Asignar el método de pago al TPV/kiosco

1. **Punto de Venta → Configuración → Punto de Venta** (o Tiendas)
2. Abrir la configuración del TPV
3. En **Métodos de Pago**, añadir el método Redsys creado
4. Guardar y cerrar/reabrir sesión POS

---

## Flujo de pago

### En kiosco self-order (`/pos-self/`)

```
Cliente toca "Pagar" en kiosco
    ↓
Kiosco JS llama POST /kiosk/payment/<pos_config_id>/kiosk
    ↓
Odoo backend: _payment_request_from_kiosk(order)
    ↓
redsys_send_payment() → POST https://<proxy>/pay (bloqueante, espera tarjeta)
    ↓
Proxy → TpvpcWinService → Verifone P400 → Cliente pasa tarjeta
    ↓
Resultado: {authorized: true, receipt: "..."}
    ↓
Odoo: order.add_payment() + action_pos_order_paid() + _send_payment_result('Success')
    ↓
Kiosco muestra pantalla de confirmación
```

### En POS regular (`/pos/ui`)

```
Cajero selecciona pago Redsys
    ↓
PaymentRedsys.sendPaymentRequest() → POST /pos/redsys/pay (JS → Odoo backend)
    ↓
redsys_send_payment() → POST https://<proxy>/pay
    ↓
Proxy → TpvpcWinService → Verifone P400
    ↓
{authorized: true} → POS marca línea de pago como "done"
```

---

## Solución de problemas

### "Ocurrió un error" en el kiosco

El proxy retorna `authorized: false`. Causas y soluciones:

1. **Proxy no arrancado** — verificar `http://localhost:8765/health` desde el kiosco
2. **Tunnel Cloudflare caído** — `sc query cloudflared` en el kiosco → si no está RUNNING, `sc start cloudflared`
3. **Sesión Redsys inválida** — el proxy hace re-login automáticamente, pero puede fallar si la cuenta está bloqueada (ver abajo)
4. **TpvpcWinService parado** — `sc query TpvpcWinService` → `sc start TpvpcWinService`

### Cuenta Redsys bloqueada

**Síntoma**: El proxy devuelve error de autenticación; en `canales.redsys.es` la cuenta aparece bloqueada.

**Causa**: Demasiados logins fallidos o repetidos. El proxy cachea la sesión (TTL 1800s) para mitigarlo, pero si se reinicia el proxy muchas veces en poco tiempo puede ocurrir.

**Solución**: Llamar al soporte CaixaBank/Redsys (902 190 191) para desbloquear la cuenta.

**Prevención**: No reiniciar el proxy innecesariamente. La caché de sesión evita logins repetidos.

### Token del tunnel Cloudflare inválido

**Síntoma**: `sc query cloudflared` muestra STOPPED; en logs de cloudflared: `"Unauthorized: Invalid tunnel secret"`.

**Causa**: El tunnel fue recreado en el dashboard de Cloudflare, invalidando el token anterior.

**Solución**:
```bat
REM Desinstalar servicio anterior
C:\cloudflared\cloudflared.exe service uninstall

REM Reinstalar con el nuevo token (obtenido desde Cloudflare → Tunnel → "Add connector")
C:\cloudflared\cloudflared.exe service install <NUEVO_TOKEN>

sc start cloudflared
```

### El campo `use_payment_terminal` está vacío en la BD

**Síntoma**: El kiosco llega a la pantalla de pago pero la tarjeta no se activa (no se llama al proxy).

**Causa**: El método de pago existía antes de instalar el módulo, por lo que el `onchange` nunca se disparó.

**Solución**: Editar el método de pago → desmarcar "Habilitar Redsys TPV-PC" → guardar → volver a marcarlo → guardar. El `onchange` setea automáticamente `use_payment_terminal = redsys_tpvpc`.

Verificar en modo desarrollador que el campo `use_payment_terminal` muestra **"Redsys TPV-PC"**.

### Timeout en el pago

**Síntoma**: El pago da timeout sin que el cliente haya podido insertar la tarjeta.

**Soluciones**:
- Aumentar **Timeout** en configuración del método de pago (recomendado: 60-90s)
- Verificar que el datáfono no está en estado de error (reiniciar terminal físicamente)
- Reinicializar el datáfono vía proxy:
```bash
curl -X POST "http://localhost:8765/reinit?comercio=369559141&terminal=1&com_port=COM9:,19200,N,8,1"
```

### El proxy no puede conectar con TpvpcWinService

**Síntoma**: `/health` responde pero `/pay` devuelve error de inicialización.

**Verificar**:
```bat
REM TpvpcWinService corriendo?
sc query TpvpcWinService

REM Puerto management accesible?
netstat -an | findstr 10200

REM Puerto pinpad accesible?
netstat -an | findstr 9000
```

Si los puertos no responden, reiniciar TpvpcWinService:
```bat
sc stop TpvpcWinService
sc start TpvpcWinService
```

---

## Referencia técnica

### Endpoints del proxy

| Endpoint | Método | Descripción |
|----------|--------|-------------|
| `/health` | GET | Estado del proxy y sesión Redsys |
| `/pay` | POST | Iniciar cobro (bloqueante hasta respuesta del terminal) |
| `/cancel` | POST | Cancelar cobro en curso |
| `/reinit` | POST | Reinicializar conexión con el datáfono |

### Endpoints Odoo

| Endpoint | Método | Auth | Descripción |
|----------|--------|------|-------------|
| `/pos/redsys/pay` | POST (JSON-RPC) | user | Cobro desde POS regular |
| `/pos/redsys/cancel` | POST (JSON-RPC) | user | Cancelar desde POS regular |
| `/pos/redsys/download_proxy` | GET | user | Descarga ZIP del proxy preconfigurado |

### Caché de sesión Redsys

El proxy mantiene el `jsessionid` de `canales.redsys.es` en memoria con TTL de 1800 segundos (30 minutos). Al expirar, hace re-login automático antes del siguiente pago. Esto evita hacer login en cada transacción, lo que bloquearía la cuenta por exceso de autenticaciones.

El endpoint `/health` muestra el estado de la caché:
```json
{
    "status": "ok",
    "session_cached": true,
    "session_ttl_remaining_s": 1250
}
```

### Variables de entorno del módulo Odoo (campos en pos.payment.method)

| Campo ORM | Tipo | Descripción |
|-----------|------|-------------|
| `redsys_enabled` | Boolean | Activa la integración |
| `redsys_proxy_url` | Char | URL del proxy (tunnel o IP local) |
| `redsys_comercio` | Char | Código de comercio del banco |
| `redsys_terminal` | Char | Número de terminal |
| `redsys_com_port` | Char | Puerto COM del datáfono |
| `redsys_usuario` | Char | Usuario `canales.redsys.es` |
| `redsys_password` | Char | Password `canales.redsys.es` |
| `redsys_timeout` | Integer | Timeout en segundos |
| `redsys_proxy_status` | Selection | Estado último test de conexión |
| `use_payment_terminal` | Selection | Seteado automáticamente a `redsys_tpvpc` |

### Compatibilidad de terminales

| Terminal | Firmware | Estado |
|----------|----------|--------|
| Verifone P400 | Redsys / CaixaBank | ✅ Probado |
| Verifone VX820 | Redsys | ✅ Compatible (mismo protocolo) |
| Otros terminales Redsys TPV-PC | Redsys | ✅ Compatible |

Requiere **TpvpcWinService v3000+** instalado por el banco adquirente.

### Estructura del módulo

```
pos_payment_redsys_tpvpc/
├── __manifest__.py               # Dependencias: point_of_sale, pos_self_order
├── controllers/
│   └── main.py                   # Endpoints /pos/redsys/*
├── models/
│   └── pos_payment_method.py     # Campos + _payment_request_from_kiosk() + redsys_send_payment()
├── static/
│   ├── proxy/
│   │   ├── proxy.py              # Proxy FastAPI para Windows
│   │   ├── config.json           # Configuración del proxy
│   │   ├── requirements.txt      # Dependencias Python del proxy
│   │   └── install.bat           # Instalador Windows
│   └── src/js/
│       └── payment_redsys.js     # PaymentInterface para POS regular
├── views/
│   └── pos_payment_method_views.xml
└── security/
    └── ir.model.access.csv
```
