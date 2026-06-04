@echo off
setlocal enabledelayedexpansion

:: Force working directory to the location of this script
cd /d "%~dp0"


echo [1/1] Normalizing data for all classes...

set "PYTHONPATH=%~dp0"
python -m crawler.normalize normalize ^
    --in data/raw ^
    --out data/normalized ^
    --class all

if %errorlevel% neq 0 (
    echo.
    echo ERROR: Normalization failed with exit code %errorlevel%.
    goto :end
)

echo.
echo ===============================================
echo   Normalization completed successfully!
echo ===============================================
echo.
echo Fresh normalized data is ready in:
echo   - data\normalized   (processed data for the optimizer)
echo.

:end
endlocal
pause
