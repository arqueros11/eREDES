@echo off
REM Construye eREDES para Windows 10/11 (ejecutar en Windows con Python 3.10+ instalado).
cd /d "%~dp0"
if not exist .venv python -m venv .venv
.venv\Scripts\pip install -q -r requirements.txt || goto :error
.venv\Scripts\python packaging\make_icons.py || goto :error
.venv\Scripts\pyinstaller --noconfirm --clean --distpath dist --workpath build packaging\eREDES.spec || goto :error
REM Carpeta DRIVERS junto al ejecutable (biblioteca local del tecnico)
xcopy /E /I /Y DRIVERS dist\eREDES\DRIVERS >nul
copy /Y LEEME-WINDOWS.txt dist\eREDES\LEEME.txt >nul
powershell -NoProfile -Command "Compress-Archive -Force -Path dist\eREDES -DestinationPath dist\eREDES-Windows.zip"
REM Acceso directo en el escritorio (solo al compilar en el PC del tecnico, no en GitHub)
if "%GITHUB_ACTIONS%"=="" powershell -NoProfile -Command "$s=(New-Object -ComObject WScript.Shell).CreateShortcut([Environment]::GetFolderPath('Desktop')+'\eREDES.lnk'); $s.TargetPath='%~dp0dist\eREDES\eREDES.exe'; $s.WorkingDirectory='%~dp0dist\eREDES'; $s.IconLocation='%~dp0dist\eREDES\eREDES.exe,0'; $s.Save()"
echo.
echo Listo: dist\eREDES\eREDES.exe  (acceso directo "eREDES" creado en el Escritorio)
echo Para otros PCs copie solo la carpeta dist\eREDES
goto :eof
:error
echo ERROR en la construccion
exit /b 1
