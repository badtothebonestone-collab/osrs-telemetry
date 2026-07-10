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
if /I "%MODE%"=="replay" goto replay
if /I "%MODE%"=="test" goto test

echo Usage: run.cmd [plugin^|observe^|task^|login COMx^|execute COMx^|replay^|test] 1>&2
exit /b 2

:plugin
call gradlew.bat run --console=plain --no-daemon
exit /b %ERRORLEVEL%

:observe
python -m osrs_bot observe
exit /b %ERRORLEVEL%

:task
python -m osrs_bot task
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
python -m osrs_bot task --execute --arduino-port "%~2"
exit /b %ERRORLEVEL%

:replay
python -m unittest -v tests.test_golden_replay
exit /b %ERRORLEVEL%

:test
python -m unittest discover -s tests -v
if ERRORLEVEL 1 exit /b %ERRORLEVEL%
call gradlew.bat test --console=plain --no-daemon
exit /b %ERRORLEVEL%
