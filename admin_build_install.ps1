# admin_build_install.ps1
$ErrorActionPreference = "Stop"
$LogFile = Join-Path $PSScriptRoot "build_log.txt"

Start-Transcript -Path $LogFile -Force

try {
    Write-Host "=============================================="
    Write-Host "Elevated Build and Install (NicoNico Danmaku)"
    Write-Host "=============================================="

    # 1. Administrator Check
    $isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
    if (-not $isAdmin) {
        Write-Host "[ERROR] This script must be run as Administrator." -ForegroundColor Red
        Exit
    }

    # 2. Junction setup
    $JunctionPath = "D:\AI\OBS_DM_Build"
    $TargetRamDisk = "K:\build\obs-niconico-danmaku"

    if (-not (Test-Path $TargetRamDisk)) {
        New-Item -ItemType Directory -Path $TargetRamDisk -Force | Out-Null
        Write-Host "[INFO] Created RAMDisk build folder: $TargetRamDisk"
    }

    if (Test-Path $JunctionPath) {
        $item = Get-Item $JunctionPath
        if ($item.Attributes -match "ReparsePoint") {
            Write-Host "[INFO] Reparse point already exists: $JunctionPath"
        } else {
            Write-Host "[WARN] $JunctionPath exists but is not a junction. Removing..." -ForegroundColor Yellow
            Remove-Item $JunctionPath -Recurse -Force
            New-Item -ItemType Junction -Path $JunctionPath -Value $TargetRamDisk | Out-Null
            Write-Host "[INFO] Created junction: $JunctionPath -> $TargetRamDisk"
        }
    } else {
        New-Item -ItemType Junction -Path $JunctionPath -Value $TargetRamDisk | Out-Null
        Write-Host "[INFO] Created junction: $JunctionPath -> $TargetRamDisk"
    }

    # 3. Configure CMake
    Write-Host "[INFO] Configuring CMake..."
    $BuildDir = "$JunctionPath\build"
    if (-not (Test-Path $BuildDir)) {
        New-Item -ItemType Directory -Path $BuildDir -Force | Out-Null
    }
    
    cmake -S $PSScriptRoot -B $BuildDir -A x64

    # 4. Build Plugin
    Write-Host "[INFO] Building plugin..."
    cmake --build $BuildDir --config Release --clean-first

    # 5. Copy DLL and Data
    $src = "$BuildDir\output\Release"
    $obsDir = "C:\Program Files\obs-studio"
    $dllName = "obs-niconico-danmaku.dll"
    $pluginName = "obs-niconico-danmaku"

    if (-not (Test-Path "$src\$dllName")) {
        Write-Host "[ERROR] Build failed or DLL not found at: $src\$dllName" -ForegroundColor Red
        Exit
    }

    Write-Host "[INFO] Copying files to OBS..."
    $targetDllDir = "$obsDir\obs-plugins\64bit"
    $targetDataDir = "$obsDir\data\obs-plugins\$pluginName\locale"

    if (-not (Test-Path $targetDllDir)) {
        New-Item -ItemType Directory -Path $targetDllDir -Force | Out-Null
    }
    if (-not (Test-Path $targetDataDir)) {
        New-Item -ItemType Directory -Path $targetDataDir -Force | Out-Null
    }

    Copy-Item "$src\$dllName" "$targetDllDir\$dllName" -Force
    Write-Host "[SUCCESS] Copied $dllName" -ForegroundColor Green

    $srcLocale = Join-Path $PSScriptRoot "data\locale"
    if (Test-Path $srcLocale) {
        Copy-Item "$srcLocale\en-US.ini" "$targetDataDir\en-US.ini" -Force
        Copy-Item "$srcLocale\ja-JP.ini" "$targetDataDir\ja-JP.ini" -Force
        Write-Host "[SUCCESS] Copied localization files." -ForegroundColor Green
    } else {
        Write-Host "[WARN] Source locale directory not found: $srcLocale" -ForegroundColor Yellow
    }

    Write-Host ""
    Write-Host "=============================================="
    Write-Host "[SUCCESS] Build & Installation Completed!" -ForegroundColor Green
    Write-Host "Please restart OBS Studio." -ForegroundColor Cyan
    Write-Host "=============================================="
} catch {
    Write-Host "[ERROR] Operation failed: $_" -ForegroundColor Red
} finally {
    Stop-Transcript
}
