$ErrorActionPreference = "Stop"

$RunnerRoot = "C:\HiStrA-Runner"
$ServerUrl = "https://histra.bonatte.cloud"
$ApiToken = "REPLACE_WITH_SERVER_TOKEN"
$RunnerId = "mauricio-pc"
$RunnerName = "Mauricio PC"

$env:HISTRA_SERVER_URL = $ServerUrl
$env:HISTRA_API_TOKEN = $ApiToken
$env:HISTRA_RUNNER_ID = $RunnerId
$env:HISTRA_RUNNER_NAME = $RunnerName
$env:HISTRA_WORK_ROOT = Join-Path $RunnerRoot "work"

New-Item -ItemType Directory -Force -Path (Join-Path $RunnerRoot "logs") | Out-Null
New-Item -ItemType Directory -Force -Path $env:HISTRA_WORK_ROOT | Out-Null

$Executable = Join-Path $RunnerRoot ".venv\Scripts\histra-runner.exe"
$Log = Join-Path $RunnerRoot "logs\runner.log"

& $Executable worker `
    --poll-interval 5 `
    --retry-interval 15 `
    --heartbeat-interval 30 `
    *>> $Log
