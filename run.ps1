# run.ps1 — start the University Requirement Reader locally
# Usage:  right-click > Run with PowerShell,  OR  in a terminal:  .\run.ps1
$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot

# 1. Make sure no old server is holding the port
Get-Process python, python3 -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue

# 2. Use the project's virtual environment if it exists
$py = "python"
if (Test-Path ".\venv\Scripts\python.exe") { $py = ".\venv\Scripts\python.exe" }

# 3. Ensure dependencies are installed (quiet; skips if already satisfied)
& $py -m pip install -q -r requirements.txt

# 4. Start the server (no --reload, so it serves the fresh page reliably)
Write-Host "`n  Dashboard:  http://127.0.0.1:8000/dashboard" -ForegroundColor Green
Write-Host "  Log in with the super-admin email/password from your .env file`n" -ForegroundColor Green
& $py -m uvicorn app.main:app --host 127.0.0.1 --port 8000
