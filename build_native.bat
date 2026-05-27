@echo off
setlocal

set "ROOT=%~dp0"

cmake -S "%ROOT%native" -B "%ROOT%build\native"
if errorlevel 1 exit /b 1

cmake --build "%ROOT%build\native" --config Release --target paragon_optimize
if errorlevel 1 exit /b 1

if not exist "%ROOT%bin\paragon_optimize.exe" (
  echo paragon_optimize.exe was not produced.
  exit /b 1
)

echo built %ROOT%bin\paragon_optimize.exe

endlocal
