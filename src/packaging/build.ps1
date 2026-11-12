<#
.SYNOPSIS
  Builds Orchestrium.exe (PyInstaller onedir) and, if Inno Setup is installed,
  the Orchestrium-Setup-<version>.exe installer.

.DESCRIPTION
  Everything happens under -BuildRoot (default: <project>\build, git-ignored except
  its .gitkeep), in its own venv -- your dev env\ is never touched.

    <BuildRoot>\venv\             build-only venv (auto-created; re-synced when requirements.txt changes)
    <BuildRoot>\vendor\ffmpeg\    ffmpeg.exe + ffprobe.exe + DLLs (auto-downloaded on first run)
    <BuildRoot>\dist\Orchestrium\ the built app (run Orchestrium.exe from here to test)

  License texts the build ships (GPL/LGPL, sfizz, FFmpeg) live in git, in
  src\packaging\licenses\.
    <BuildRoot>\installer\        the installer
    <BuildRoot>\work\, smoke\     scratch

  Run from anywhere:
    src\packaging\build.bat                      # build app + installer (bundles VPO)
    src\packaging\build.bat -SmokeTest           # also run the full pipeline headlessly in a frozen build first
    src\packaging\build.bat -NoVpo               # installer without the VPO component (~1 GB smaller)
    src\packaging\build.bat -Version 1.2.0 -Clean
#>
param(
    [string]$Version = (Get-Date -Format "yyyy.M.d"),
    [string]$BuildRoot = "",
    [string]$Python = "",
    [string]$SfizzExe = "",
    [string]$VpoDir = "",
    # FFmpeg 8.x LGPL *shared* build (no --enable-gpl, must have --enable-libmp3lame).
    # Only downloaded when <BuildRoot>\vendor\ffmpeg\ffmpeg.exe is missing. If you
    # move to a new FFmpeg version, update licenses\ffmpeg\SOURCE.txt to match.
    [string]$FfmpegUrl = "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-n8.1-latest-win64-lgpl-shared-8.1.zip",
    [switch]$NoVpo,
    [switch]$NoInstaller,
    [switch]$SmokeTest,
    [switch]$Clean
)

$ErrorActionPreference = "Stop"
$Src = Split-Path $PSScriptRoot -Parent          # ...\Orchestrium\src
$Project = Split-Path $Src -Parent               # ...\Orchestrium
if (-not $BuildRoot) { $BuildRoot = Join-Path $Project "build" }
if (-not $SfizzExe) { $SfizzExe = Join-Path $Project "bin\sfizz_render_1.2.2.exe" }
if (-not $VpoDir) { $VpoDir = Join-Path $Project "VPO" }
$Vendor = Join-Path $BuildRoot "vendor"
$Licenses = Join-Path $PSScriptRoot "licenses"
$FfmpegDir = Join-Path $Vendor "ffmpeg"
$Venv = Join-Path $BuildRoot "venv"
$VenvPy = Join-Path $Venv "Scripts\python.exe"
$Spec = Join-Path $PSScriptRoot "Orchestrium.spec"

function Step($msg) { Write-Host "`n=== $msg" -ForegroundColor Cyan }
function Fail($msg) { Write-Host "BUILD FAILED: $msg" -ForegroundColor Red; exit 1 }
function Check-Exit($what) { if ($LASTEXITCODE -ne 0) { Fail "$what (exit code $LASTEXITCODE)" } }

# --- Preflight ---------------------------------------------------------------
Step "Preflight"
$required = @(
    @{ Path = $SfizzExe; Hint = "sfizz_render executable (pass -SfizzExe)" },
    @{ Path = (Join-Path $Licenses "GPL-3.0.txt"); Hint = "src\packaging\licenses\GPL-3.0.txt" },
    @{ Path = (Join-Path $Licenses "LGPL-3.0.txt"); Hint = "src\packaging\licenses\LGPL-3.0.txt" },
    @{ Path = (Join-Path $Licenses "sfizz\LICENSE.txt"); Hint = "src\packaging\licenses\sfizz\LICENSE.txt" },
    @{ Path = (Join-Path $Licenses "ffmpeg\LICENSE.txt"); Hint = "src\packaging\licenses\ffmpeg\LICENSE.txt" },
    @{ Path = (Join-Path $Project "assets\icon.ico"); Hint = "assets\icon.ico" },
    @{ Path = (Join-Path $Project "requirements.txt"); Hint = "requirements.txt" }
)
foreach ($r in $required) { if (-not (Test-Path $r.Path)) { Fail "missing $($r.Hint)" } }
$BundleVpo = (-not $NoVpo) -and (-not $NoInstaller)
if ($BundleVpo -and -not (Test-Path (Join-Path $VpoDir "Strings\all-strings-SEC-normal-mod-wheel.sfz"))) {
    Fail "VPO not found at $VpoDir (pass -VpoDir, or -NoVpo for an installer without it)"
}
Write-Host "Version:    $Version"
Write-Host "Build root: $BuildRoot"

# --- Build venv --------------------------------------------------------------
Step "Build venv"
if (-not (Test-Path $VenvPy)) {
    if (-not $Python) {
        if (Get-Command py -ErrorAction SilentlyContinue) {
            $Python = (& py -3.11 -c "import sys; print(sys.executable)").Trim()
        } elseif (Test-Path "C:\Program Files\Python311\python.exe") {
            $Python = "C:\Program Files\Python311\python.exe"
        } else { Fail "no Python 3.11 found (pass -Python <path to python.exe>)" }
    }
    Write-Host "Creating venv with $Python"
    & $Python -m venv $Venv; Check-Exit "venv creation"
}
$reqFile = Join-Path $Project "requirements.txt"
$reqHash = (Get-FileHash $reqFile -Algorithm SHA256).Hash + "|pyinstaller"
$hashFile = Join-Path $Venv ".requirements.sha256"
if (-not (Test-Path $hashFile) -or (Get-Content $hashFile -Raw).Trim() -ne $reqHash) {
    Write-Host "Installing requirements + PyInstaller (first run takes a while)..."
    & $VenvPy -m pip install --quiet --upgrade pip; Check-Exit "pip upgrade"
    & $VenvPy -m pip install --quiet -r $reqFile pyinstaller; Check-Exit "pip install"
    Set-Content $hashFile $reqHash
} else { Write-Host "Up to date." }

# --- FFmpeg (download once) --------------------------------------------------
Step "FFmpeg"
if ((Test-Path (Join-Path $FfmpegDir "ffmpeg.exe")) -and (Test-Path (Join-Path $FfmpegDir "ffprobe.exe"))) {
    Write-Host "Up to date ($FfmpegDir)."
} else {
    Write-Host "Downloading $FfmpegUrl"
    $tmp = Join-Path $Vendor "_ffmpeg_download"
    Remove-Item $tmp -Recurse -Force -ErrorAction SilentlyContinue
    New-Item -ItemType Directory -Force $tmp | Out-Null
    try {
        $ProgressPreference = "SilentlyContinue"   # PS 5.1's progress bar makes downloads crawl
        Invoke-WebRequest $FfmpegUrl -OutFile (Join-Path $tmp "ffmpeg.zip") -UseBasicParsing
        Expand-Archive (Join-Path $tmp "ffmpeg.zip") -DestinationPath (Join-Path $tmp "x") -Force
        $binDir = Get-ChildItem (Join-Path $tmp "x") -Recurse -Directory -Filter bin | Select-Object -First 1
        if (-not $binDir) { Fail "no bin\ folder in the FFmpeg zip" }
        # Check the build's configure flags by reading the DLL's text -- nothing is executed.
        $avcodec = Get-ChildItem $binDir.FullName -Filter "avcodec-*.dll" | Select-Object -First 1
        if (-not $avcodec) { Fail "not a shared FFmpeg build (no avcodec-*.dll); use a *-shared* zip" }
        $text = [System.Text.Encoding]::ASCII.GetString([System.IO.File]::ReadAllBytes($avcodec.FullName))
        if (-not $text.Contains("--enable-libmp3lame")) { Fail "this FFmpeg build can't encode MP3 (no --enable-libmp3lame)" }
        if ($text.Contains("--enable-gpl")) { Fail "this is a GPL FFmpeg build; use an LGPL one (-FfmpegUrl)" }
        Remove-Item $FfmpegDir -Recurse -Force -ErrorAction SilentlyContinue
        New-Item -ItemType Directory -Force $FfmpegDir | Out-Null
        Get-ChildItem $binDir.FullName -File -Include *.exe, *.dll -Recurse |
            Where-Object { $_.Name -ne "ffplay.exe" } | Copy-Item -Destination $FfmpegDir
        Write-Host "Installed FFmpeg into $FfmpegDir"
    } finally {
        Remove-Item $tmp -Recurse -Force -ErrorAction SilentlyContinue
    }
}

# --- PyInstaller -------------------------------------------------------------
function Invoke-PyInstaller($entry, $name, $distPath, $workPath, $manifest) {
    $env:ORCH_ENTRY = $entry; $env:ORCH_NAME = $name; $env:ORCH_MANIFEST = $manifest
    try {
        $piArgs = @("-m", "PyInstaller", $Spec, "--noconfirm", "--log-level", "WARN",
                    "--distpath", $distPath, "--workpath", $workPath)
        if ($Clean) { $piArgs += "--clean" }
        & $VenvPy @piArgs; Check-Exit "PyInstaller ($name)"
    } finally {
        Remove-Item Env:ORCH_ENTRY, Env:ORCH_NAME, Env:ORCH_MANIFEST -ErrorAction SilentlyContinue
    }
}

function Copy-Runtime($appDir) {
    # bin\ and assets\ live beside the exe (app_paths.PROJECT_ROOT = exe folder).
    $bin = Join-Path $appDir "bin"
    New-Item -ItemType Directory -Force $bin | Out-Null
    Copy-Item $SfizzExe $bin -Force
    Get-ChildItem $FfmpegDir -File -Include *.exe, *.dll -Recurse |
        Where-Object { $_.Name -ne "ffplay.exe" } | Copy-Item -Destination $bin -Force
    Copy-Item (Join-Path $Project "assets") $appDir -Recurse -Force
}

$Dist = Join-Path $BuildRoot "dist"
$App = Join-Path $Dist "Orchestrium"
$Work = Join-Path $BuildRoot "work"

if ($SmokeTest) {
    Step "Smoke test: building headless test exe"
    $smokeDist = Join-Path $BuildRoot "smoke\dist"
    Invoke-PyInstaller "packaging\smoke_test.py" "smoke_test" $smokeDist (Join-Path $BuildRoot "smoke\work") ""
    $smokeApp = Join-Path $smokeDist "smoke_test"
    Copy-Runtime $smokeApp
    $out = Join-Path $BuildRoot "smoke\out"
    Remove-Item $out -Recurse -Force -ErrorAction SilentlyContinue
    $raw = Join-Path $Project "data\raw"
    $smokeArgs = @("$raw\SOLO.mid", "$raw\ORCHESTRA.mid", "$raw\REC.mp3", $VpoDir, $out) |
        ForEach-Object { '"' + $_ + '"' }
    Step "Smoke test: running the full pipeline (a few minutes)"
    Start-Process (Join-Path $smokeApp "smoke_test.exe") -ArgumentList $smokeArgs -Wait -NoNewWindow
    $log = Join-Path $out "smoke_test.log"
    if (-not (Test-Path $log)) { Fail "smoke test produced no log" }
    $tail = Get-Content $log -Tail 25
    if ($tail -notcontains "PIPELINE OK") { $tail | Write-Host; Fail "smoke test failed (full log: $log)" }
    Write-Host "PIPELINE OK" -ForegroundColor Green
}

Step "Building Orchestrium.exe"
if (Test-Path $App) { Remove-Item $App -Recurse -Force }
$manifest = Join-Path $Work "bundled_files.json"
New-Item -ItemType Directory -Force $Work | Out-Null
Invoke-PyInstaller "orchestrium.py" "Orchestrium" $Dist $Work $manifest
Copy-Runtime $App

Step "Collecting third-party licenses"
& $VenvPy (Join-Path $PSScriptRoot "collect_licenses.py") $manifest $App $Licenses $Project
Check-Exit "license collection"

$appMB = [math]::Round((Get-ChildItem $App -Recurse -File | Measure-Object Length -Sum).Sum / 1MB)
Write-Host "App folder: $App ($appMB MB)"

# --- Installer ---------------------------------------------------------------
if ($NoInstaller) { Write-Host "`nDone (installer skipped). Run: $App\Orchestrium.exe" -ForegroundColor Green; exit 0 }

Step "Building installer"
# Any Inno Setup version (6, 7, ...), newest first.
$iscc = @($env:ProgramFiles, ${env:ProgramFiles(x86)}, "$env:LOCALAPPDATA\Programs") |
    Where-Object { $_ -and (Test-Path $_) } |
    ForEach-Object { Get-ChildItem $_ -Directory -Filter "Inno Setup*" -ErrorAction SilentlyContinue } |
    Sort-Object Name -Descending |
    ForEach-Object { Join-Path $_.FullName "ISCC.exe" } |
    Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $iscc) { $iscc = (Get-Command iscc -ErrorAction SilentlyContinue).Source }
if (-not $iscc) {
    Write-Host "Inno Setup not found -- skipping the installer. Install it with:" -ForegroundColor Yellow
    Write-Host "  winget install --id JRSoftware.InnoSetup -e" -ForegroundColor Yellow
    Write-Host "App is still usable from: $App" -ForegroundColor Green
    exit 0
}
$installerDir = Join-Path $BuildRoot "installer"
$isccArgs = @("/Q", "/DAppVersion=$Version", "/DSourceDir=$App", "/DOutputDir=$installerDir")
if ($BundleVpo) { $isccArgs += "/DVpoDir=$VpoDir"; Write-Host "Bundling VPO from $VpoDir (compression takes several minutes)" }
& $iscc @isccArgs (Join-Path $PSScriptRoot "installer.iss"); Check-Exit "Inno Setup"
$setup = Join-Path $installerDir "Orchestrium-Setup-$Version.exe"
Write-Host "`nDone: $setup ($([math]::Round((Get-Item $setup).Length / 1MB)) MB)" -ForegroundColor Green
