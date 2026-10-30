# Windows-os megfelelője az init-env.sh-nak.
# Létrehozza a .env fájlt az .env.example alapján, és kitölti a titkokat.
# Meglévő .env-et nem ír felül.
#
# Futtatás (PowerShellben, ebben a mappában):
#   powershell -ExecutionPolicy Bypass -File .\init-env.ps1

$ErrorActionPreference = 'Stop'
Set-Location -Path $PSScriptRoot

if (Test-Path -Path '.env') {
    Write-Host 'A .env már létezik, nem írom felül.'
    exit 0
}

function New-Hex {
    $bytes = New-Object byte[] 32
    $rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    $rng.GetBytes($bytes)
    $rng.Dispose()
    return -join ($bytes | ForEach-Object { $_.ToString('x2') })
}

$utf8NoBom = New-Object System.Text.UTF8Encoding $false
$examplePath = Join-Path $PSScriptRoot '.env.example'
$envPath = Join-Path $PSScriptRoot '.env'

$content = [System.IO.File]::ReadAllText($examplePath, [System.Text.Encoding]::UTF8)

$aioPass = (New-Hex).Substring(0, 20)

$content = $content -replace '(?m)^STREMHU_SESSION_SECRET=.*$', ('STREMHU_SESSION_SECRET=' + (New-Hex))
$content = $content -replace '(?m)^AIOSTREAMS_SECRET_KEY=.*$', ('AIOSTREAMS_SECRET_KEY=' + (New-Hex))
$content = $content -replace '(?m)^AIOSTREAMS_AUTH=.*$', ('AIOSTREAMS_AUTH=admin:' + $aioPass)

# UTF-8 BOM nélkül, mert a Docker Compose a BOM-ot nem szereti.
[System.IO.File]::WriteAllText($envPath, $content, $utf8NoBom)

Write-Host 'Kész: .env létrehozva.'
Write-Host ("AIOStreams bejelentkezés:  admin / " + $aioPass)
Write-Host 'Most írd be a .env-be a TAILNET és TS_AUTHKEY értékét:  notepad .env'
