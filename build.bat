@echo off
setlocal EnableDelayedExpansion
title CreoVCS Build

echo ============================================================
echo  CreoVCS Build Pipeline
echo  Stage 1 : Copy source tree to dist_obf\
echo  Stage 2 : PyArmor  (critical files only)
echo  Stage 3 : PyInstaller  (EXE packaging)
echo ============================================================
echo.

:: ── Prerequisites check ───────────────────────────────────────────────────
where python      >nul 2>&1 || (echo [ERROR] Python not found on PATH & exit /b 1)
where pip         >nul 2>&1 || (echo [ERROR] pip not found on PATH & exit /b 1)

:: ── Install / upgrade build dependencies ──────────────────────────────────
echo [1/6] Installing dependencies...
pip install -q -r requirements.txt
if errorlevel 1 (echo [ERROR] pip install failed & exit /b 1)

where pyarmor     >nul 2>&1 || (echo [ERROR] pyarmor not found after install & exit /b 1)
where pyinstaller >nul 2>&1 || (echo [ERROR] pyinstaller not found after install & exit /b 1)

:: ── Clean previous outputs ────────────────────────────────────────────────
echo [2/6] Cleaning previous build artefacts...
if exist dist_obf  rmdir /s /q dist_obf
if exist build     rmdir /s /q build
if exist dist      rmdir /s /q dist

:: ── Copy full source tree to dist_obf (plain Python baseline) ────────────
echo [3/6] Copying source tree to dist_obf\...
robocopy . dist_obf /e ^
    /xd dist_obf build dist .git tools __pycache__ ^
    /xf *.pyc *.spec build.bat build_excludes.txt requirements.txt ^
    /njh /njs /ndl /nc /ns
:: robocopy exit codes: 0-7 = success, 8+ = error
if errorlevel 8 (echo [ERROR] robocopy failed & exit /b 1)

:: ── PyArmor: obfuscate ONLY critical files ────────────────────────────────
echo [4/6] Obfuscating critical files with PyArmor...
pyarmor gen ^
    --output dist_obf ^
    main3.py ^
    config.py ^
    core\workspace_config.py ^
    core\session_manager.py ^
    core\licensing\__init__.py ^
    core\licensing\manager.py ^
    core\licensing\loader.py ^
    core\licensing\crypto.py ^
    core\licensing\machine.py ^
    core\licensing\payload.py ^
    core\licensing\checks.py ^
    core\licensing\exceptions.py ^
    core\licensing\revocation.py ^
    core\licensing\version_check.py
if errorlevel 1 (echo [ERROR] PyArmor obfuscation failed & exit /b 1)

echo      Protected: main3.py, config.py, core\licensing\, core\workspace_config.py
echo      Plain Python: pages\, core\services\, core\repositories\, core\models\

:: ── Copy assets into obfuscated tree ─────────────────────────────────────
:: (robocopy already copied assets\ — only need modern_theme.qss separately)
echo [5/6] Copying extra assets...
if exist modern_theme.qss copy /y modern_theme.qss dist_obf\ >nul

:: ── PyInstaller: package into single EXE ─────────────────────────────────
echo [6/6] Building EXE with PyInstaller...
pyinstaller --clean --noconfirm creovcs.spec
if errorlevel 1 (echo [ERROR] PyInstaller build failed & exit /b 1)

:: ── Done ──────────────────────────────────────────────────────────────────
echo.
echo ============================================================
echo  BUILD SUCCESSFUL
echo  Output : dist\CreoVCS.exe
echo ============================================================
echo.

explorer dist

endlocal
