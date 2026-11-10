@echo off
REM ---------------------------------------------------------------
REM  Build DentecLab.exe on Windows 10/11.
REM  Requires Python 3.12+ (python.org) on PATH.
REM  Optional: install Tesseract OCR first so it is bundled.
REM ---------------------------------------------------------------
cd /d "%~dp0"
python -m venv .venv || goto :error
call .venv\Scripts\activate.bat
python -m pip install --upgrade pip
python -m pip install -r requirements-dev.txt || goto :error
python -m pytest -q || goto :error
pyinstaller --noconfirm DentecLab.spec || goto :error
xcopy sample_data dist\DentecLab\sample_data /E /I /Y >nul
xcopy docs dist\DentecLab\docs /E /I /Y >nul
copy README.md dist\DentecLab\ >nul
if exist "C:\Program Files\Tesseract-OCR\tesseract.exe" (
    echo Bundling Tesseract OCR...
    xcopy "C:\Program Files\Tesseract-OCR" dist\DentecLab\tesseract /E /I /Y >nul
)
dist\DentecLab\DentecLab.exe --self-test
type dentec_selftest.txt
echo.
echo Build complete: dist\DentecLab\DentecLab.exe
goto :eof
:error
echo BUILD FAILED
exit /b 1
