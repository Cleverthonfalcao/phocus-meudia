@echo off
echo Iniciando Phocus Meu Dia...
echo Acesse: http://localhost:5200
echo.
echo Para parar o servidor, feche esta janela ou pressione CTRL+C
echo.

cd /d "%~dp0"

:: Tenta pip install silencioso
pip install flask --quiet 2>nul

python app.py
pause
