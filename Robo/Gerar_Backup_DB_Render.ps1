$ErrorActionPreference = "Stop"

$connectionFile = Join-Path $PSScriptRoot "Senha_db_render.txt"
$outputFile = "C:\Users\NPO\Downloads\genesis-db.dump"

if (-not (Test-Path -LiteralPath $connectionFile)) {
    throw "Arquivo de conexão não encontrado: $connectionFile"
}

$dbUrl = (Get-Content -LiteralPath $connectionFile -Raw).Trim()
if (-not $dbUrl.StartsWith("postgres://") -and -not $dbUrl.StartsWith("postgresql://")) {
    throw "Senha_db_render.txt não contém uma External Database URL válida."
}

Write-Host "Gerando backup somente leitura do banco Render..."
docker run --rm -v "C:\Users\NPO\Downloads:/backup" postgres:18 pg_dump "--dbname=$dbUrl" --format=c --file=/backup/genesis-db.dump --no-owner --no-privileges
if ($LASTEXITCODE -ne 0) {
    throw "O pg_dump falhou. O arquivo não foi considerado válido."
}

$dump = Get-Item -LiteralPath $outputFile -ErrorAction Stop
if ($dump.Length -le 0) {
    throw "O backup foi criado vazio. Verifique a External Database URL."
}

Write-Host "Backup criado com sucesso: $($dump.FullName)"
Write-Host "Tamanho: $($dump.Length) bytes"
