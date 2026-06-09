@echo off
setlocal

set "ROOT=%~dp0"
set "EXE=%ROOT%bin\paragon_optimize.exe"

if not exist "%EXE%" (
  call "%ROOT%build_native.bat"
  if errorlevel 1 exit /b 1
)

"%EXE%" optimize --profile "%ROOT%profiles\druid.example.json" --max-routes 0 --candidate-targets 0
pause

endlocal
