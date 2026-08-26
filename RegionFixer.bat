@echo off
setlocal
cd /d "%~dp0"

if "%~1"=="" goto no_args

where py >nul 2>&1
if %errorlevel%==0 (
    py -3 "%~dp0regionfixer.py" %*
    set "regionfixer_exit=%errorlevel%"
    goto finished
)

where python >nul 2>&1
if %errorlevel%==0 (
    python "%~dp0regionfixer.py" %*
    set "regionfixer_exit=%errorlevel%"
    goto finished
)

echo.
echo ERROR: Python 3 was not found.
echo Install Python 3, then try again.
echo.
pause
exit /b 1

:finished
echo.
echo ========================================================================
echo Region Fixer finished. Review the scan summary above.
echo Exit code: %regionfixer_exit%
echo ========================================================================
echo.
echo Press any key to close this window...
pause >nul
exit /b %regionfixer_exit%

:no_args
echo.
echo Minecraft Region Fixer Modern
echo.
echo Drag a Minecraft world folder onto RegionFixer.bat to scan it.
echo.
echo Or run from Terminal, for example:
echo   RegionFixer.bat "C:\path\to\world"
echo.
echo This launcher forwards all arguments to regionfixer.py.
echo After a scan it waits for a keypress so the summary stays visible.
echo.
pause
exit /b 0
