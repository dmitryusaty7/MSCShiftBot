$ErrorActionPreference = 'Stop'

$scriptPath = $MyInvocation.MyCommand.Path
$scriptDirectory = Split-Path -Parent $scriptPath
$projectRoot = Split-Path -Parent $scriptDirectory

Set-Location -Path $projectRoot

$envFilePath = Join-Path -Path $projectRoot -ChildPath '.env'
if (Test-Path -Path $envFilePath) {
    Get-Content -Path $envFilePath | ForEach-Object {
        $line = $_.Trim()
        if ($line -and -not $line.StartsWith('#')) {
            $parts = $line -split '=', 2
            if ($parts.Length -eq 2) {
                $name = $parts[0].Trim()
                $value = $parts[1].Trim()
                if ($name) {
                    [Environment]::SetEnvironmentVariable($name, $value, 'Process')
                }
            }
        }
    }
}

$venvPath = Join-Path -Path $projectRoot -ChildPath '.venv'
$activateDir = Join-Path -Path $venvPath -ChildPath 'Scripts'
$activateScript = Join-Path -Path $activateDir -ChildPath 'Activate.ps1'

if (Test-Path -Path $activateScript) {
    . $activateScript
}

$botScript = Join-Path -Path $projectRoot -ChildPath 'bot_shift.py'

& python $botScript @args
