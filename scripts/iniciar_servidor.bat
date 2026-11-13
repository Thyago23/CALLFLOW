@echo off
rem Inicia CALLFLOW para que se pueda abrir desde otras PCs de la red (0.0.0.0:8000).
rem Doble clic en este archivo y deje la ventana abierta mientras se use la app.
rem Para detenerla: Ctrl + C o cerrar la ventana.
title CALLFLOW - servidor en el puerto 8000
cd /d "%~dp0.."
for /f "tokens=2 delims=:" %%a in ('ipconfig ^| findstr /c:"IPv4"') do echo Abrir en: http:%%a:8000 | findstr "192.168"
echo En esta PC tambien: http://127.0.0.1:8000
echo.
python manage.py runserver 0.0.0.0:8000 --noreload
pause
