<#!
.SYNOPSIS
Creates PettyFlow's local virtual environment using a supported Python version.

.DESCRIPTION
Python 3.12 is the preferred version. Python 3.11 through 3.13 are accepted
so contributors can adopt a newer supported runtime without changing the script.
Set PETTYFLOW_PYTHON or pass -Python to select a particular interpreter.
#>
[CmdletBinding()]
param(
    [string]$Python = $env:PETTYFLOW_PYTHON,
    [switch]$SkipInstall
)

$ErrorActionPreference = "Stop"
$RepositoryRoot = Split-Path -Parent $PSScriptRoot
$VenvPath = Join-Path $RepositoryRoot ".venv"

if (-not $Python) {
    $Python = "python"
}

try {
    $VersionText = (& $Python -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')").Trim()
} catch {
    throw "Unable to run Python interpreter '$Python'. Set PETTYFLOW_PYTHON or use -Python."
}

$Version = [version]$VersionText
if ($Version -lt [version]"3.11" -or $Version -ge [version]"3.14") {
    throw "Python $VersionText is unsupported. Use Python 3.11 through 3.13."
}

if (Test-Path $VenvPath) {
    throw ".venv already exists. Activate it or remove it manually before recreating it."
}

Write-Host "[INFO] Creating .venv with Python $VersionText..."
& $Python -m venv $VenvPath
if ($LASTEXITCODE -ne 0) {
    throw "Virtual environment creation failed."
}

if (-not $SkipInstall) {
    $VenvPython = Join-Path $VenvPath "Scripts\python.exe"
    Write-Host "[INFO] Installing project dependencies..."
    & $VenvPython -m pip install --upgrade pip
    if ($LASTEXITCODE -ne 0) {
        throw "pip upgrade failed."
    }
    & $VenvPython -m pip install -r (Join-Path $RepositoryRoot "requirements.txt")
    if ($LASTEXITCODE -ne 0) {
        throw "Dependency installation failed. Check network access and rerun the command."
    }
}

Write-Host "[SUCCESS] Activate with: .\.venv\Scripts\Activate.ps1"
