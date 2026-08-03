<#
.SYNOPSIS
    Production Nuitka build script for Nexus PDM.

.DESCRIPTION
    Builds the Nexus desktop app with Nuitka standalone mode, collects assets
    and native DLLs, optionally builds the OpenCascade CAD Viewer, and produces
    a clean release folder.

    The script is designed for large Python environments. It explicitly avoids
    following heavy scientific/interactive libraries that are not part of the
    Nexus runtime, while still allowing the CAD Viewer build to include OCC.

.EXAMPLES
    # Recommended production build
    powershell -ExecutionPolicy Bypass -File .\build_nuitka.ps1 -Clean

    # Use a specific Python environment
    powershell -ExecutionPolicy Bypass -File .\build_nuitka.ps1 -Python "C:\Users\mkazi\miniconda3\envs\pyoccenv\python.exe" -Clean

    # Build only the main Nexus app, without the CAD Viewer/OCC runtime
    powershell -ExecutionPolicy Bypass -File .\build_nuitka.ps1 -Clean -SkipCadViewer

    # Faster development build, less optimized
    powershell -ExecutionPolicy Bypass -File .\build_nuitka.ps1 -BuildProfile Fast -Clean

.NOTES
    Output:
        dist_nuitka\Nexus-release\Nexus.exe

    Do not ship local databases or licenses from the repository root.
    Production database and license files should stay in the configured
    workspace/user data location.
#>

[CmdletBinding()]
param(
    [string]$Python = "",
    [ValidateSet("Release", "Fast")]
    [string]$BuildProfile = "Release",
    [string]$OutputRoot = "",
    [switch]$Clean,
    [switch]$SkipCadViewer,
    [switch]$SkipPipInstall,
    [switch]$SmokeTest,
    [switch]$VerboseNuitka
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
if (-not $OutputRoot) {
    $OutputRoot = Join-Path $Root "dist_nuitka"
}
$BuildLogRoot = Join-Path $OutputRoot "logs"
$ReleaseRoot = Join-Path $OutputRoot "Nexus-release"
$MainEntry = Join-Path $Root "main3.py"
$CadEntry = Join-Path $Root "tools\CAD\step_viewer\__main__.py"
$IconPath = Join-Path $Root "assets\pictures\nexus_logo.ico"
$ThemePath = Join-Path $Root "modern_theme.qss"
$MainDist = Join-Path $OutputRoot "main3.dist"
$CadDist = Join-Path $OutputRoot "__main__.dist"
$MainExeName = "Nexus.exe"
$CadExeName = "CADViewer.exe"

function Write-Stage {
    param([string]$Message)
    Write-Host ""
    Write-Host "========================================================================" -ForegroundColor DarkCyan
    Write-Host " $Message" -ForegroundColor Cyan
    Write-Host "========================================================================" -ForegroundColor DarkCyan
}

function Resolve-PythonExecutable {
    param([string]$Requested)
    if ($Requested) {
        $resolved = [System.IO.Path]::GetFullPath($Requested)
        if (-not (Test-Path -LiteralPath $resolved)) {
            throw "Python executable not found: $resolved"
        }
        return $resolved
    }

    $candidates = @(
        (Join-Path $env:USERPROFILE "miniconda3\envs\pyoccenv\python.exe"),
        (Join-Path $env:USERPROFILE "anaconda3\envs\pyoccenv\python.exe"),
        (Join-Path $env:USERPROFILE "miniconda3\python.exe"),
        (Join-Path $env:USERPROFILE "AppData\Local\Programs\Python\Python313\python.exe"),
        (Join-Path $env:USERPROFILE "AppData\Local\Programs\Python\Python312\python.exe"),
        (Join-Path $env:USERPROFILE "AppData\Local\Programs\Python\Python311\python.exe")
    )
    foreach ($candidate in $candidates) {
        if ($candidate -and (Test-Path -LiteralPath $candidate)) {
            return [System.IO.Path]::GetFullPath($candidate)
        }
    }

    $cmd = Get-Command python -ErrorAction SilentlyContinue
    if ($cmd -and $cmd.Source) {
        return [System.IO.Path]::GetFullPath($cmd.Source)
    }
    throw "Python was not found. Pass -Python C:\path\to\python.exe"
}

function Invoke-Checked {
    param(
        [Parameter(Mandatory=$true)][string]$Title,
        [Parameter(Mandatory=$true)][string]$FilePath,
        [Parameter(Mandatory=$true)][string[]]$Arguments,
        [string]$LogPath = ""
    )
    Write-Host "[run] $Title" -ForegroundColor Yellow
    if ($VerboseNuitka) {
        Write-Host "$FilePath $($Arguments -join ' ')" -ForegroundColor DarkGray
    }
    if ($LogPath) {
        & $FilePath @Arguments 2>&1 | Tee-Object -FilePath $LogPath
    } else {
        & $FilePath @Arguments
    }
    if ($LASTEXITCODE -ne 0) {
        throw "$Title failed with exit code $LASTEXITCODE"
    }
}

function Assert-WorkspacePath {
    param([string]$Path)
    $resolved = [System.IO.Path]::GetFullPath($Path)
    $resolvedRoot = [System.IO.Path]::GetFullPath($Root)
    if (-not $resolved.StartsWith($resolvedRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to clean path outside workspace: $resolved"
    }
    return $resolved
}

function Remove-SafeDirectory {
    param([string]$Path)
    if (-not (Test-Path -LiteralPath $Path)) {
        return
    }
    $resolved = Assert-WorkspacePath $Path
    Remove-Item -LiteralPath $resolved -Recurse -Force
}

function Copy-DirectoryContents {
    param(
        [Parameter(Mandatory=$true)][string]$Source,
        [Parameter(Mandatory=$true)][string]$Destination
    )
    if (-not (Test-Path -LiteralPath $Source)) {
        return
    }
    New-Item -ItemType Directory -Path $Destination -Force | Out-Null
    Get-ChildItem -LiteralPath $Source -Force | ForEach-Object {
        Copy-Item -LiteralPath $_.FullName -Destination $Destination -Recurse -Force
    }
}

function Get-PythonInfo {
    param([string]$PythonExe)
    $code = @"
import json, os, sys, sysconfig
print(json.dumps({
    "executable": sys.executable,
    "version": sys.version,
    "prefix": sys.prefix,
    "base_prefix": sys.base_prefix,
    "platlib": sysconfig.get_paths().get("platlib", ""),
    "scripts": sysconfig.get_path("scripts") or "",
}))
"@
    $json = & $PythonExe -c $code
    if ($LASTEXITCODE -ne 0) {
        throw "Could not inspect Python environment."
    }
    return $json | ConvertFrom-Json
}

function Test-PythonModule {
    param([string]$PythonExe, [string]$Module)
    & $PythonExe -c "import $Module" *> $null
    return ($LASTEXITCODE -eq 0)
}

function Copy-NativeDlls {
    param(
        [Parameter(Mandatory=$true)][string]$PythonExe,
        [Parameter(Mandatory=$true)][string]$Destination,
        [switch]$IncludeOCC
    )
    $pyInfo = Get-PythonInfo $PythonExe
    $prefix = [string]$pyInfo.prefix
    $candidateDirs = @(
        (Join-Path $prefix "Library\bin"),
        (Join-Path $prefix "DLLs"),
        (Split-Path -Parent $PythonExe)
    ) | Where-Object { $_ -and (Test-Path -LiteralPath $_) } | Select-Object -Unique

    $runtimePatterns = @(
        "vcruntime*.dll",
        "msvcp*.dll",
        "concrt*.dll",
        "libcrypto*.dll",
        "libssl*.dll",
        "sqlite3.dll",
        "zlib*.dll",
        "libffi*.dll"
    )
    $occPatterns = @(
        "TK*.dll",
        "TKernel.dll",
        "freetype*.dll",
        "freeimage*.dll",
        "tbb*.dll",
        "jemalloc*.dll",
        "gl2ps*.dll"
    )
    $patterns = @() + $runtimePatterns
    if ($IncludeOCC) {
        $patterns += $occPatterns
    }

    $copied = 0
    foreach ($dir in $candidateDirs) {
        foreach ($pattern in $patterns) {
            Get-ChildItem -LiteralPath $dir -Filter $pattern -File -ErrorAction SilentlyContinue | ForEach-Object {
                $target = Join-Path $Destination $_.Name
                if (-not (Test-Path -LiteralPath $target) -or ((Get-Item -LiteralPath $target).Length -ne $_.Length)) {
                    Copy-Item -LiteralPath $_.FullName -Destination $target -Force
                    $copied++
                }
            }
        }
    }
    Write-Host "[dll] Native DLL collection completed. Copied/updated: $copied" -ForegroundColor DarkGreen
}

function New-ReleaseManifest {
    param([string]$Destination, [string]$PythonExe, [string]$Profile)
    $files = Get-ChildItem -LiteralPath $Destination -Recurse -File
    $totalBytes = ($files | Measure-Object Length -Sum).Sum
    $manifest = [ordered]@{
        app = "Nexus PDM"
        built_at = (Get-Date).ToString("yyyy-MM-dd HH:mm:ss")
        build_profile = $Profile
        python = $PythonExe
        file_count = @($files).Count
        size_mb = [Math]::Round(($totalBytes / 1MB), 1)
        entry = "Nexus.exe"
    }
    $manifest | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath (Join-Path $Destination "build_manifest.json") -Encoding UTF8
}

Set-Location $Root
$Python = Resolve-PythonExecutable $Python
$PythonInfo = Get-PythonInfo $Python
$Jobs = [Math]::Max(1, [Environment]::ProcessorCount - 1)

Write-Stage "Nexus Nuitka Build"
Write-Host "Root        : $Root"
Write-Host "Python      : $Python"
Write-Host "Python ver  : $($PythonInfo.version.Split([Environment]::NewLine)[0])"
Write-Host "Output root : $OutputRoot"
Write-Host "Profile     : $BuildProfile"
Write-Host "Jobs        : $Jobs"

foreach ($required in @($MainEntry, $IconPath, $ThemePath)) {
    if (-not (Test-Path -LiteralPath $required)) {
        throw "Required build input missing: $required"
    }
}

if ($Clean) {
    Write-Stage "Cleaning previous Nuitka output"
    Remove-SafeDirectory $OutputRoot
}
New-Item -ItemType Directory -Path $OutputRoot -Force | Out-Null
New-Item -ItemType Directory -Path $BuildLogRoot -Force | Out-Null

Write-Stage "Checking build dependencies"
if (-not $SkipPipInstall) {
    Invoke-Checked "Install/upgrade Nuitka toolchain" $Python @(
        "-m", "pip", "install", "--disable-pip-version-check", "--upgrade",
        "nuitka", "ordered-set", "zstandard", "wheel"
    ) (Join-Path $BuildLogRoot "pip-build-deps.log")
}
foreach ($module in @("nuitka", "ordered_set", "zstandard", "PyQt5")) {
    if (-not (Test-PythonModule $Python $module)) {
        throw "Required Python module is missing in build environment: $module"
    }
}

$CommonArgs = @(
    "--mode=standalone",
    "--assume-yes-for-downloads",
    "--msvc=latest",
    "--jobs=$Jobs",
    "--output-dir=$OutputRoot",
    "--enable-plugin=pyqt5",
    "--include-qt-plugins=sensible",
    "--python-flag=no_docstrings",
    "--python-flag=no_asserts",
    "--noinclude-pytest-mode=nofollow",
    "--noinclude-setuptools-mode=nofollow",
    "--nofollow-import-to=tkinter",
    "--nofollow-import-to=unittest",
    "--nofollow-import-to=test",
    "--nofollow-import-to=tests",
    "--nofollow-import-to=IPython",
    "--nofollow-import-to=notebook",
    "--nofollow-import-to=jupyter",
    "--nofollow-import-to=matplotlib",
    "--nofollow-import-to=pandas",
    "--nofollow-import-to=scipy",
    "--nofollow-import-to=sklearn",
    "--nofollow-import-to=pytest",
    "--nofollow-import-to=PIL.ImageQt",
    "--deployment"
)

if ($BuildProfile -eq "Release") {
    $CommonArgs += @("--lto=yes")
} else {
    $CommonArgs += @("--lto=no")
}

Write-Stage "Compiling Nexus main application"
$MainArgs = @($CommonArgs) + @(
    "--windows-console-mode=disable",
    "--windows-icon-from-ico=$IconPath",
    "--include-data-dir=$Root\assets\pictures=assets/pictures",
    "--include-data-files=$ThemePath=modern_theme.qss",
    "--include-package=core",
    "--include-package=pages",
    "--include-package=setup",
    "--include-package=openpyxl",
    "--include-module=utils",
    "--include-module=config",
    "--include-module=tools.CAD.step_viewer.launcher",
    "--include-package=tools.CAD.step_diff_engine",
    "--nofollow-import-to=OCC",
    "--nofollow-import-to=tools.CAD.step_viewer.main_window",
    "--nofollow-import-to=tools.CAD.step_diff_engine.step_diff_gui",
    "--output-filename=$MainExeName",
    "--report=$OutputRoot\nuitka-main-report.xml",
    $MainEntry
)
Invoke-Checked "Nuitka main build" $Python (@("-m", "nuitka") + $MainArgs) (Join-Path $BuildLogRoot "nuitka-main.log")

if (-not $SkipCadViewer) {
    if (-not (Test-Path -LiteralPath $CadEntry)) {
        throw "CAD Viewer entry point missing: $CadEntry"
    }
    if (-not (Test-PythonModule $Python "OCC")) {
        throw "OCC module is missing. Use the pyoccenv environment or pass -SkipCadViewer."
    }
    Write-Stage "Compiling CAD Viewer with OpenCascade"
    $CadArgs = @($CommonArgs) + @(
        "--windows-console-mode=disable",
        "--windows-icon-from-ico=$IconPath",
        "--include-package=tools.CAD.step_viewer",
        "--include-package=tools.CAD.step_diff_engine",
        "--include-package=OCC",
        "--output-filename=$CadExeName",
        "--report=$OutputRoot\nuitka-cad-report.xml",
        $CadEntry
    )
    Invoke-Checked "Nuitka CAD Viewer build" $Python (@("-m", "nuitka") + $CadArgs) (Join-Path $BuildLogRoot "nuitka-cad.log")
}

Write-Stage "Assembling release folder"
Remove-SafeDirectory $ReleaseRoot
New-Item -ItemType Directory -Path $ReleaseRoot -Force | Out-Null
Copy-DirectoryContents $MainDist $ReleaseRoot

if (-not $SkipCadViewer) {
    if (-not (Test-Path -LiteralPath $CadDist)) {
        throw "Expected CAD Viewer distribution not found: $CadDist"
    }
    Copy-DirectoryContents $CadDist $ReleaseRoot
}

Copy-NativeDlls -PythonExe $Python -Destination $ReleaseRoot -IncludeOCC:(!$SkipCadViewer)

# Never ship local development DB/license/trail files by accident.
foreach ($unsafe in @("creo_vcs.db", "creovcs.db", "creovcs.lic", "trail.txt.1")) {
    $candidate = Join-Path $ReleaseRoot $unsafe
    if (Test-Path -LiteralPath $candidate) {
        Remove-Item -LiteralPath $candidate -Force
    }
}

$MainExe = Join-Path $ReleaseRoot $MainExeName
if (-not (Test-Path -LiteralPath $MainExe)) {
    throw "Expected output executable not found: $MainExe"
}

New-ReleaseManifest -Destination $ReleaseRoot -PythonExe $Python -Profile $BuildProfile

if ($SmokeTest) {
    Write-Stage "Smoke test"
    $proc = Start-Process -FilePath $MainExe -WorkingDirectory $ReleaseRoot -PassThru -WindowStyle Hidden
    Start-Sleep -Seconds 5
    if (-not $proc.HasExited) {
        Stop-Process -Id $proc.Id -Force
        Write-Host "[smoke] Nexus started and stayed alive for 5 seconds." -ForegroundColor Green
    } elseif ($proc.ExitCode -ne 0) {
        throw "Smoke test failed. Nexus exited with code $($proc.ExitCode)"
    }
}

$files = Get-ChildItem -LiteralPath $ReleaseRoot -Recurse -File
$sizeBytes = ($files | Measure-Object Length -Sum).Sum
$sizeMb = [Math]::Round($sizeBytes / 1MB, 1)

Write-Stage "BUILD SUCCESSFUL"
Write-Host "Executable : $MainExe" -ForegroundColor Green
Write-Host "Release dir: $ReleaseRoot" -ForegroundColor Green
Write-Host "Size       : $sizeMb MB"
Write-Host "Files      : $(@($files).Count)"
Write-Host "Logs       : $BuildLogRoot"
