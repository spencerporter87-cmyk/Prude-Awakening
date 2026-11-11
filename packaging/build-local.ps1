# Builds the Prude Awakening installer on THIS computer (no GitHub Actions minutes needed).
#
#   1. Install Python 3.12 (python.org) and Inno Setup 6 (https://jrsoftware.org/isdl.php, or:
#      winget install JRSoftware.InnoSetup)
#   2. Open PowerShell in the project folder and run:   .\packaging\build-local.ps1
#
# The first run downloads about 1.5 GB of packages and takes 15-25 minutes; later runs reuse them.
# Result: dist\Prude-Awakening-Setup-<version>.exe
param([switch]$SkipTests, [switch]$SkipSelfTest)

$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")
$env:PYTHONUTF8 = "1"

function Step($t) { Write-Host "`n=== $t ===" -ForegroundColor Cyan }
function Run($exe, [string[]]$a) {
    & $exe @a
    if ($LASTEXITCODE -ne 0) { throw "$exe $($a -join ' ') failed (exit code $LASTEXITCODE)" }
}

Step "Checking Python 3.12"
$py = $null
try { if ((Get-Command py -ErrorAction SilentlyContinue) -and ("$(& py -3.12 --version 2>&1)" -match "3\.12")) { $py = @("py", "-3.12") } } catch {}
try { if (-not $py -and (Get-Command python -ErrorAction SilentlyContinue) -and ("$(& python --version 2>&1)" -match "3\.12")) { $py = @("python") } } catch {}
if (-not $py) { throw "Python 3.12 was not found. Install it from python.org (tick 'Add python.exe to PATH') and run this again." }
$pyArgs = if ($py.Length -gt 1) { $py[1..($py.Length - 1)] } else { @() }

$iscc = @("${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe", "$env:ProgramFiles\Inno Setup 6\ISCC.exe",
          "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe") | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $iscc) { throw "Inno Setup 6 was not found. Install it (winget install JRSoftware.InnoSetup) and run this again." }

Step "Setting up a private Python environment (.venv-build)"
if (-not (Test-Path ".venv-build\Scripts\python.exe")) { Run $py[0] ($pyArgs + @("-m", "venv", ".venv-build")) }
$vp = ".venv-build\Scripts\python.exe"
Run $vp @("-m", "pip", "install", "--upgrade", "pip")
Run $vp @("-m", "pip", "install", "-r", "requirements.txt", "pyinstaller", "pytest")
# picture-subtitle text recognition: --no-deps so it doesn't pull in the full opencv next to the headless one
Run $vp @("-m", "pip", "install", "--no-deps", "rapidocr_onnxruntime>=1.3,<1.5")
Run $vp @("-m", "pip", "install", "pyclipper", "shapely", "pyyaml", "six", "pillow")

Step "FFmpeg"
if (-not (Test-Path "vendor\ffmpeg\bin\ffmpeg.exe")) { & "$PSScriptRoot\get_ffmpeg.ps1" } else { Write-Host "Already downloaded." }

if (-not $SkipTests) {
    Step "Unit tests"
    Run $vp @("-m", "pytest", "tests", "-q")
}

Step "Building PrudeAwakening.exe"
Run $vp @("-m", "PyInstaller", "packaging\prude.spec", "--noconfirm", "--distpath", "dist", "--workpath", "build")

if (-not $SkipSelfTest) {
    Step "Self-test of the built app"
    New-Item -ItemType Directory -Force out | Out-Null
    $p = Start-Process "dist\PrudeAwakening\PrudeAwakening.exe" -ArgumentList "--self-test", "$PWD\out\selftest.json" -PassThru -Wait
    if (Test-Path out\selftest.json) { Get-Content out\selftest.json }
    if ($p.ExitCode -ne 0) { throw "The built app failed its self-test." }
}

Step "Building the installer"
$ver = (Select-String -Path "src\prude\__init__.py" -Pattern '__version__ = "(.+)"').Matches[0].Groups[1].Value
Run $iscc @("/DAppVersion=$ver", "packaging\installer.iss")
$exe = Get-ChildItem "dist\Prude-Awakening-Setup-*.exe" | Sort-Object LastWriteTime | Select-Object -Last 1
Write-Host "`nDone: $($exe.FullName)" -ForegroundColor Green
