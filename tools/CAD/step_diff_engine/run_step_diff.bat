@echo off
setlocal

REM Usage:
REM run_step_diff.bat <step_a> <step_b> <commit_a> <commit_b> [output_json] [metadata_json]

if "%~1"=="" goto :usage
if "%~2"=="" goto :usage
if "%~3"=="" goto :usage
if "%~4"=="" goto :usage

set "STEP_A=%~1"
set "STEP_B=%~2"
set "COMMIT_A=%~3"
set "COMMIT_B=%~4"
set "OUTPUT_JSON=%~5"
set "METADATA_JSON=%~6"

if "%OUTPUT_JSON%"=="" set "OUTPUT_JSON=S:\MKworld\creo vcs\creo_vcs_v4\tools\CAD\step_diff_engine\last_diff.json"

set "CONDA_BAT=C:\Users\mkazi\miniconda3\condabin\conda.bat"
if not exist "%CONDA_BAT%" (
  echo [ERROR] conda.bat not found at "%CONDA_BAT%"
  exit /b 1
)

set "SCRIPT_DIR=%~dp0"
for %%I in ("%SCRIPT_DIR%..\..\..") do set "REPO_ROOT=%%~fI"

echo [INFO] Running STEP diff...
pushd "%REPO_ROOT%"
if not "%METADATA_JSON%"=="" (
  call "%CONDA_BAT%" run -n pyoccenv python -m tools.CAD.step_diff_engine compare --step-a "%STEP_A%" --step-b "%STEP_B%" --commit-a "%COMMIT_A%" --commit-b "%COMMIT_B%" --output "%OUTPUT_JSON%" --metadata "%METADATA_JSON%"
) else (
  call "%CONDA_BAT%" run -n pyoccenv python -m tools.CAD.step_diff_engine compare --step-a "%STEP_A%" --step-b "%STEP_B%" --commit-a "%COMMIT_A%" --commit-b "%COMMIT_B%" --output "%OUTPUT_JSON%"
)
popd
set "RC=%ERRORLEVEL%"
if not "%RC%"=="0" (
  echo [ERROR] Diff failed with code %RC%.
  exit /b %RC%
)

echo [OK] Diff created: "%OUTPUT_JSON%"
exit /b 0

:usage
echo Usage:
echo   run_step_diff.bat ^<step_a^> ^<step_b^> ^<commit_a^> ^<commit_b^> [output_json] [metadata_json]
echo.
echo Example:
echo   run_step_diff.bat "C:\Users\mkazi\OneDrive\Desktop\step folder\step1.step" "C:\Users\mkazi\OneDrive\Desktop\step folder\step2.step" step1 step2
exit /b 1
