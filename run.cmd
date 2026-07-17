@echo off
setlocal
cd /d "%~dp0"

set "MODE=%~1"
if "%MODE%"=="" set "MODE=observe"

if /I "%MODE%"=="plugin" goto plugin
if /I "%MODE%"=="observe" goto observe
if /I "%MODE%"=="task" goto task
if /I "%MODE%"=="login" goto login
if /I "%MODE%"=="execute" goto execute
if /I "%MODE%"=="record-demo" goto record_demo
if /I "%MODE%"=="inspect-demo" goto inspect_demo
if /I "%MODE%"=="app" goto app
if /I "%MODE%"=="gui" goto gui
if /I "%MODE%"=="replay" goto replay
if /I "%MODE%"=="test" goto test
if /I "%MODE%"=="help" goto help
if /I "%MODE%"=="--help" goto help

echo Unknown command: %MODE% 1>&2
goto help_error

:help
echo OSRS Automation Engine
echo.
echo   run.cmd gui                    Launch the operator desktop application
echo   run.cmd plugin                 Launch the RuneLite development client
echo   run.cmd observe                Read one loaded-scene observation
echo   run.cmd task [--overlay]       Propose one action without gameplay input
echo   run.cmd execute COMx [--overlay]
echo                                  Run the validated profile through Arduino
echo   run.cmd login COMx             Recover a saved authenticated session
echo   run.cmd record-demo NAME [options]
echo   run.cmd inspect-demo PATH
echo   run.cmd app COMMAND [options]  Use the diagnostic application CLI
echo   run.cmd replay                 Run golden cycle and retained camera replays
echo   run.cmd test                   Run the Python and Java suites
exit /b 0

:help_error
call "%~f0" help 1>&2
exit /b 2

:plugin
call gradlew.bat run --console=plain --no-daemon
exit /b %ERRORLEVEL%

:observe
python -m osrs_bot observe
exit /b %ERRORLEVEL%

:task
python -m osrs_bot task %~2 %~3
exit /b %ERRORLEVEL%

:login
if "%~2"=="" (
    echo Login assistance requires an Arduino port, for example: run.cmd login COM6 1>&2
    exit /b 2
)
python -m osrs_bot.login --arduino-port "%~2"
exit /b %ERRORLEVEL%

:execute
if "%~2"=="" (
    echo Live execution requires an Arduino port, for example: run.cmd execute COM6 1>&2
    exit /b 2
)
python -m osrs_bot task --execute --arduino-port "%~2" %~3 %~4
exit /b %ERRORLEVEL%

:record_demo
if "%~2"=="" (
    echo Demonstration capture requires a safe name, for example: run.cmd record-demo castle-stairs 1>&2
    exit /b 2
)
python -m osrs_bot.demonstration record "%~2" %3 %4 %5 %6 %7 %8 %9
exit /b %ERRORLEVEL%

:inspect_demo
if "%~2"=="" (
    echo Demonstration inspection requires an artifact path. 1>&2
    exit /b 2
)
python -m osrs_bot.demonstration inspect "%~2"
exit /b %ERRORLEVEL%

:app
if "%~2"=="" (
    echo Application facade requires a command: catalog, profile-schema, validate-profile, or run. 1>&2
    exit /b 2
)
python -m osrs_bot.application_cli %2 %3 %4 %5 %6 %7 %8 %9
exit /b %ERRORLEVEL%

:gui
python -m osrs_bot.gui
exit /b %ERRORLEVEL%

:replay
python -m unittest -v tests.test_golden_replay tests.test_camera_replay
exit /b %ERRORLEVEL%

:test
python -m unittest discover -s tests -v
if ERRORLEVEL 1 exit /b %ERRORLEVEL%
call gradlew.bat test --console=plain --no-daemon
exit /b %ERRORLEVEL%
