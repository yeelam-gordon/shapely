@echo off
setlocal EnableExtensions EnableDelayedExpansion

:: Build and install GEOS on Windows system, save cache for later
::
:: This script requires environment variables to be set
::  - set GEOS_INSTALL=C:\path\to\cached\prefix -- to build or use as cache
::  - set GEOS_VERSION=3.13.1 -- to download and compile
::  - set GEOS_SHA256=<authoritative 64-hex digest> -- to verify before extraction

if not defined GEOS_INSTALL exit /B 10
if not defined GEOS_VERSION exit /B 10
if not defined GEOS_SHA256 exit /B 10

powershell.exe -NoProfile -NonInteractive -Command "$expected=$env:GEOS_SHA256; if($expected -notmatch '^[0-9A-Fa-f]{64}$'){throw 'GEOS_SHA256 must be exactly 64 hexadecimal characters.'}"
if errorlevel 1 exit /B 10

set "GEOS_ARCHIVE=geos-%GEOS_VERSION%.tar.bz2"
set "GEOS_URL=https://download.osgeo.org/geos/%GEOS_ARCHIVE%"
set "GEOS_CACHE_MARKER=%GEOS_INSTALL%\.geos-source-sha256"

if exist "%GEOS_INSTALL%" (
  if not exist "%GEOS_CACHE_MARKER%" exit /B 13
  set "CACHED_GEOS_SHA256="
  set /P CACHED_GEOS_SHA256=<"%GEOS_CACHE_MARKER%"
  if /I not "!CACHED_GEOS_SHA256!"=="!GEOS_SHA256!" exit /B 13
  if not exist "%GEOS_INSTALL%\include\geos_c.h" exit /B 13
  if not exist "%GEOS_INSTALL%\bin\geos_c.dll" exit /B 13
  echo Verified GEOS version=!GEOS_VERSION! url=!GEOS_URL! sha256=!CACHED_GEOS_SHA256!
  exit /B 0
)

echo Building %GEOS_INSTALL%

curl.exe --fail --location --proto "=https" --tlsv1.2 --retry 3 --output "%GEOS_ARCHIVE%" "%GEOS_URL%"
if errorlevel 1 (
  del /Q "%GEOS_ARCHIVE%" >NUL 2>&1
  exit /B 11
)

set "ACTUAL_GEOS_SHA256="
set "GEOS_HASH_FILE=%CD%\geos-%GEOS_VERSION%.sha256"
powershell.exe -NoProfile -NonInteractive -Command "$ErrorActionPreference='Stop'; $sha=[System.Security.Cryptography.SHA256]::Create(); try { $stream=[System.IO.File]::OpenRead('%GEOS_ARCHIVE%'); try { $hash=-join ($sha.ComputeHash($stream) | ForEach-Object { $_.ToString('x2') }); Set-Content -NoNewline -LiteralPath '%GEOS_HASH_FILE%' -Value $hash } finally { $stream.Dispose() } } finally { $sha.Dispose() }"
if errorlevel 1 (
  del /Q "%GEOS_ARCHIVE%" >NUL 2>&1
  exit /B 12
)
set /P ACTUAL_GEOS_SHA256=<"%GEOS_HASH_FILE%"
del /Q "%GEOS_HASH_FILE%" >NUL 2>&1
if not defined ACTUAL_GEOS_SHA256 (
  del /Q "%GEOS_ARCHIVE%" >NUL 2>&1
  exit /B 12
)
if /I not "!ACTUAL_GEOS_SHA256!"=="!GEOS_SHA256!" (
  del /Q "%GEOS_ARCHIVE%" >NUL 2>&1
  exit /B 12
)
echo Verified GEOS version=!GEOS_VERSION! url=!GEOS_URL! sha256=!ACTUAL_GEOS_SHA256!

7z x "%GEOS_ARCHIVE%"
if errorlevel 1 exit /B 14
7z x "geos-%GEOS_VERSION%.tar"
if errorlevel 1 exit /B 15
cd "geos-%GEOS_VERSION%" || exit /B 16

cmake -GNinja ^
  -D CMAKE_BUILD_TYPE=Release ^
  -D BUILD_SHARED_LIBS=ON ^
  -D CMAKE_INSTALL_PREFIX=%GEOS_INSTALL% ^
  -D BUILD_TESTING=OFF ^
  -S . -B build
IF %ERRORLEVEL% NEQ 0 exit /B 2

cmake --build build
IF %ERRORLEVEL% NEQ 0 exit /B 3

:: cd build
:: ctest --output-on-failure .
:: IF %ERRORLEVEL% NEQ 0 exit /B 4
:: cd ..

cmake --install build
IF %ERRORLEVEL% NEQ 0 exit /B 5

> "%GEOS_CACHE_MARKER%" echo !GEOS_SHA256!
if errorlevel 1 exit /B 6
