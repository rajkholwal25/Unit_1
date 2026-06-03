# Create all tables on unit1_combined (DATABASE_URL in .env — do not use legacy DB "unit1").
# 1. Create PostgreSQL database unit1_combined in pgAdmin (if missing).
# 2. .env must be:
#    DATABASE_URL=postgresql://postgres:YOUR_PASSWORD@localhost:5432/unit1_combined
# 3. Run from project root:
#    .\scripts\setup_new_database.ps1

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot\..

if (-not $env:FLASK_APP) { $env:FLASK_APP = "manage.py" }

Write-Host "Using DATABASE_URL from .env ..."
python -m flask setup-database
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host ""
Write-Host "Optional: sync SAP customer/item mirror (requires SAP in .env):"
Write-Host "  python -m flask sync-sap-mirror --scope customers"
