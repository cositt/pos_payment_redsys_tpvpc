@echo off
echo === Instalando Redsys TPV-PC Proxy ===

:: Verificar Python
python --version >nul 2>&1
if errorlevel 1 (
    echo ERROR: Python no encontrado. Descarga desde https://python.org
    pause
    exit /b 1
)

:: Crear entorno virtual
echo Creando entorno virtual...
python -m venv venv

:: Instalar dependencias
echo Instalando dependencias...
venv\Scripts\pip install -r requirements.txt

:: Crear servicio de arranque automatico con Task Scheduler
echo Registrando inicio automatico con Windows...
schtasks /create /tn "RedsysProxy" /tr "\"%~dp0venv\Scripts\python.exe\" \"%~dp0proxy.py\"" /sc onlogon /rl highest /f

echo.
echo === Instalacion completada ===
echo El proxy se iniciara automaticamente al arrancar Windows.
echo Para iniciarlo ahora: venv\Scripts\python proxy.py
echo URL del proxy: http://localhost:8765
echo.
pause
