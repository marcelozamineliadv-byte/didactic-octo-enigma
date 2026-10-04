@echo off
REM Gera o executavel BuscadorPublicacoesOAB.exe na pasta "dist"
REM Requer Python instalado (https://www.python.org/downloads/ - marque "Add to PATH")
python -m pip install --upgrade pyinstaller
python -m PyInstaller --onefile --windowed --name BuscadorPublicacoesOAB buscador_publicacoes.py
echo.
echo Pronto! O executavel esta em: dist\BuscadorPublicacoesOAB.exe
pause
