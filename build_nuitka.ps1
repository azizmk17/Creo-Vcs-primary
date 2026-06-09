param(
    [string]$Python = "C:\Users\mkazi\miniconda3\envs\pyoccenv\python.exe",
    [switch]$Clean,
    [switch]$SkipCadViewer
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$OutputRoot = Join-Path $Root "dist_nuitka"
$MainDist = Join-Path $OutputRoot "main3.dist"
$CadDist = Join-Path $OutputRoot "__main__.dist"

if (-not (Test-Path -LiteralPath $Python)) {
    throw "Python environment not found: $Python"
}

Set-Location $Root

if ($Clean -and (Test-Path -LiteralPath $OutputRoot)) {
    $resolvedOutput = [System.IO.Path]::GetFullPath($OutputRoot)
    $resolvedRoot = [System.IO.Path]::GetFullPath($Root)
    if (-not $resolvedOutput.StartsWith($resolvedRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to clean output outside the workspace: $resolvedOutput"
    }
    Remove-Item -LiteralPath $resolvedOutput -Recurse -Force
}

New-Item -ItemType Directory -Path $OutputRoot -Force | Out-Null
$Jobs = [Environment]::ProcessorCount

Write-Host "[1/4] Checking Nuitka build dependencies..."
& $Python -m pip install --disable-pip-version-check --upgrade nuitka ordered-set zstandard
if ($LASTEXITCODE -ne 0) { throw "Unable to install Nuitka build dependencies." }

$Common = @(
    "--mode=standalone",
    "--assume-yes-for-downloads",
    "--msvc=latest",
    "--lto=no",
    "--jobs=$Jobs",
    "--python-flag=no_docstrings",
    "--output-dir=$OutputRoot",
    "--enable-plugin=pyqt5",
    "--noinclude-pytest-mode=nofollow",
    "--noinclude-setuptools-mode=nofollow",
    "--nofollow-import-to=tkinter",
    "--nofollow-import-to=IPython",
    "--nofollow-import-to=matplotlib",
    "--nofollow-import-to=pandas",
    "--nofollow-import-to=scipy"
)

Write-Host "[2/4] Compiling CreoVCS main application..."
$MainArgs = $Common + @(
    "--windows-console-mode=disable",
    "--windows-icon-from-ico=$Root\assets\pictures\creovcs_logo-main.ico",
    "--include-data-dir=$Root\assets\pictures=assets/pictures",
    "--include-data-files=$Root\modern_theme.qss=modern_theme.qss",
    "--include-package=core",
    "--include-package=pages",
    "--include-package=setup",
    "--include-module=utils",
    "--include-module=config",
    "--include-module=tools.CAD.step_viewer.launcher",
    "--include-package=tools.CAD.step_diff_engine",
    "--nofollow-import-to=OCC",
    "--nofollow-import-to=tools.CAD.step_viewer.main_window",
    "--nofollow-import-to=tools.CAD.step_diff_engine.step_diff_gui",
    "--output-filename=CreoVCS.exe",
    "--report=$OutputRoot\nuitka-main-report.xml",
    "$Root\main3.py"
)
& $Python -m nuitka @MainArgs
if ($LASTEXITCODE -ne 0) { throw "CreoVCS Nuitka build failed." }

if (-not $SkipCadViewer) {
    Write-Host "[3/4] Compiling CAD Viewer and collecting OpenCascade DLLs..."
    $CadArgs = $Common + @(
        "--windows-console-mode=disable",
        "--windows-icon-from-ico=$Root\assets\pictures\creovcs_logo-main.ico",
        "--include-package=tools.CAD.step_viewer",
        "--include-package=tools.CAD.step_diff_engine",
        "--include-package=OCC",
        "--output-filename=CADViewer.exe",
        "--report=$OutputRoot\nuitka-cad-report.xml",
        "$Root\tools\CAD\step_viewer\__main__.py"
    )
    & $Python -m nuitka @CadArgs
    if ($LASTEXITCODE -ne 0) { throw "CAD Viewer Nuitka build failed." }

    Write-Host "[4/4] Merging CAD Viewer runtime and DLLs into the main distribution..."
    if (-not (Test-Path -LiteralPath $CadDist)) {
        throw "Expected CAD Viewer distribution not found: $CadDist"
    }
    Copy-Item -Path (Join-Path $CadDist "*") -Destination $MainDist -Recurse -Force
} else {
    Write-Host "[3/4] CAD Viewer build skipped."
    Write-Host "[4/4] Runtime merge skipped."
}

$MainExe = Join-Path $MainDist "CreoVCS.exe"
if (-not (Test-Path -LiteralPath $MainExe)) {
    throw "Expected output executable not found: $MainExe"
}

$size = (Get-ChildItem -LiteralPath $MainDist -Recurse -File | Measure-Object Length -Sum).Sum
$sizeMb = [Math]::Round($size / 1MB, 1)
Write-Host ""
Write-Host "Nuitka build complete."
Write-Host "Executable : $MainExe"
Write-Host "Distribution: $MainDist"
Write-Host "Size       : $sizeMb MB"
