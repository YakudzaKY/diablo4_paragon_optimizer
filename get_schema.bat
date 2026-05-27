@echo off
setlocal

set "ROOT=%~dp0"
set "EXE=%ROOT%bin\paragon_optimize.exe"

if not exist "%EXE%" (
  call "%ROOT%build_native.bat"
  if errorlevel 1 exit /b 1
)

"%EXE%" schema --class paladin
pause

endlocal
