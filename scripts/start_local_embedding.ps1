param(
    [string]$ServerPath = "D:\AIModels\llama.cpp\llama-server.exe",
    [string]$ModelPath = "D:\AIModels\Qwen3-Embedding-8B\Qwen3-Embedding-8B-Q4_K_M.gguf",
    [int]$Port = 8080
)

$ErrorActionPreference = "Stop"

if (-not (Test-Path -LiteralPath $ServerPath)) {
    throw "llama-server was not found: $ServerPath"
}
if (-not (Test-Path -LiteralPath $ModelPath)) {
    throw "Model weights were not found: $ModelPath"
}
$client = [System.Net.Sockets.TcpClient]::new()
$isListening = $false
try {
    $connection = $client.ConnectAsync("127.0.0.1", $Port)
    $isListening = $connection.Wait(500) -and $client.Connected
}
catch {
    $isListening = $false
}
finally {
    $client.Dispose()
}
if ($isListening) {
    Write-Output "Local embedding server is already listening on port $Port."
    exit 0
}

$logDirectory = "D:\AIModels\logs"
New-Item -ItemType Directory -Force -Path $logDirectory | Out-Null
$arguments = @(
    "-m", $ModelPath,
    "--embedding",
    "--pooling", "last",
    "--host", "127.0.0.1",
    "--port", $Port,
    "-ngl", "99",
    "-c", "4096",
    "-b", "512",
    "-ub", "512",
    "--parallel", "1"
)

$process = Start-Process `
    -FilePath $ServerPath `
    -ArgumentList $arguments `
    -WorkingDirectory (Split-Path -Parent $ServerPath) `
    -WindowStyle Hidden `
    -RedirectStandardOutput (Join-Path $logDirectory "qwen-embedding.stdout.log") `
    -RedirectStandardError (Join-Path $logDirectory "qwen-embedding.stderr.log") `
    -PassThru

Write-Output "Local embedding server is starting. PID=$($process.Id), port=$Port."
