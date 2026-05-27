@echo off
setlocal enabledelayedexpansion

:: Force working directory to the location of this script
cd /d "%~dp0"

echo ===============================================
echo   Paragon Optimizer - Full Data Update
echo ===============================================
echo.

echo This script will:
echo   1. DELETE all existing raw and normalized data
echo   2. Download fresh data from Wowhead for ALL classes
echo   3. Normalize the data for use by the optimizer
echo.
echo WARNING: This process can take a very long time (hours)
echo          depending on your internet speed and Wowhead limits.
echo.

set /p CONFIRM="Do you want to continue? (Y/N): "
if /i not "!CONFIRM!"=="Y" (
    echo Operation cancelled.
    goto :end
)

echo.
echo [1/3] Cleaning old data...

if exist "data\raw" (
    echo     Removing data\raw ...
    rmdir /s /q "data\raw" 2>nul
)
if exist "data\normalized" (
    echo     Removing data\normalized ...
    rmdir /s /q "data\normalized" 2>nul
)

echo     Data folders cleaned.
echo.

echo [2/3] Running crawler (downloading all classes)...
echo     This step is slow and may be interrupted by Wowhead rate limits.
echo.

set "PYTHONPATH=%~dp0.."

python -m paragon_optimizer.crawler.wowhead_crawler crawl ^
    --class all ^
    --out data/raw ^
    --sleep 1.5 ^
    --block-sleep 60 ^
    --retries 6 ^
    --class-sleep 10 ^
    --prefer-existing-details

if %errorlevel% neq 0 (
    echo.
    echo ERROR: Crawler failed with exit code %errorlevel%.
    echo Check the output above for details.
    goto :end
)

echo.
echo     Crawler completed successfully.
echo.

echo [3/3] Normalizing data for all classes...

python -m paragon_optimizer.crawler.normalize normalize ^
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
echo   Data update completed successfully!
echo ===============================================
echo.
echo Fresh data is ready in:
echo   - data\raw          (raw Wowhead data)
echo   - data\normalized   (processed data for the optimizer)
echo.

:end
endlocal
pause
