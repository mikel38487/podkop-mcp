#requires -Version 5.1
<#
.SYNOPSIS
  Поднимает OpenWrt в Docker и готовит SSH-доступ для тестов podkop-mcp.

.DESCRIPTION
  Это не виртуальная машина: контейнер использует ядро хоста. Но внутри —
  настоящий OpenWrt userland: uci, ubusd, rpcd, dropbear. forkop CLI/init
  заменены заглушками, чтобы проверять SSH + UCI + apply/rollback.

  Требуется запущенный Docker Desktop.

.EXAMPLE
  .\scripts\openwrt-docker.ps1
  . .\.tmp\openwrt-env.ps1
  .\.venv\Scripts\python.exe -m pytest -q -m docker_ssh

.EXAMPLE
  .\scripts\openwrt-docker.ps1 -Stop
#>
param(
    [int]$Port = 2222,
    [string]$Name = "podkop-mcp-openwrt",
    [switch]$Rebuild,
    [switch]$Stop
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$Tmp = Join-Path $Root ".tmp"
$KeyDir = Join-Path $Tmp "openwrt-ssh"
$Key = Join-Path $KeyDir "id_ed25519"
$Pub = "$Key.pub"
$Image = "podkop-mcp-openwrt:test"

if ($Stop) {
    docker rm -f $Name 2>$null | Out-Null
    Write-Host "container $Name removed"
    exit 0
}

docker info --format '{{.ServerVersion}}' 2>$null | Out-Null
if ($LASTEXITCODE -ne 0) { throw "Docker daemon недоступен. Запустите Docker Desktop." }

New-Item -ItemType Directory -Force -Path $Tmp, $KeyDir | Out-Null
if (-not (Test-Path $Key)) {
    Write-Host "generating test SSH key: $Key"
    ssh-keygen -t ed25519 -f $Key -N '' -q
}

if ($Rebuild -or -not (docker image inspect $Image 2>$null)) {
    Write-Host "building $Image"
    docker build -t $Image (Join-Path $Root "tests\docker")
    if ($LASTEXITCODE -ne 0) { throw "docker build failed" }
}

docker rm -f $Name 2>$null | Out-Null
Write-Host "starting OpenWrt container $Name on 127.0.0.1:$Port"
docker run -d --name $Name -p "${Port}:22" `
    -v "${Pub}:/etc/dropbear/authorized_keys:ro" `
    $Image | Out-Null
if ($LASTEXITCODE -ne 0) { throw "docker run failed" }

$known = Join-Path $Tmp "openwrt-known_hosts"
Remove-Item $known -ErrorAction SilentlyContinue
$deadline = (Get-Date).AddSeconds(60)
$ready = $false
while ((Get-Date) -lt $deadline) {
    Start-Sleep -Milliseconds 700
    $out = ssh -i $Key -p $Port -o BatchMode=yes -o ConnectTimeout=3 `
        -o StrictHostKeyChecking=accept-new -o UserKnownHostsFile=$known `
        root@127.0.0.1 "uci get forkop.main.domain" 2>$null
    if ($LASTEXITCODE -eq 0) { $ready = $true; break }
}
if (-not $ready) {
    docker logs $Name 2>&1 | Select-Object -Last 30
    throw "OpenWrt SSH не поднялся. Логи выше."
}

$envFile = Join-Path $Tmp "openwrt-env.ps1"
@"
`$env:PODKOP_MCP_DOCKER_SSH = "1"
`$env:PODKOP_MCP_SSH_HOST = "127.0.0.1"
`$env:PODKOP_MCP_SSH_PORT = "$Port"
`$env:PODKOP_MCP_SSH_USER = "root"
`$env:PODKOP_MCP_SSH_KEY_PATH = "$Key"
`$env:PODKOP_MCP_KNOWN_HOSTS = "$known"
`$env:PODKOP_MCP_HOST_KEY_POLICY = "reject"
`$env:PODKOP_MCP_READ_ONLY = "0"
"@ | Set-Content -Encoding UTF8 $envFile

Write-Host ""
Write-Host "OpenWrt container ready."
Write-Host "  source env:  . `"$envFile`""
Write-Host "  test:        .\.venv\Scripts\python.exe -m pytest -q -m docker_ssh"
Write-Host "  shell:       ssh -i `"$Key`" -p $Port root@127.0.0.1"
Write-Host "  stop:        .\scripts\openwrt-docker.ps1 -Stop"
