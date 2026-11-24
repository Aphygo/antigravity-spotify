$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$PluginRoot = Split-Path -Parent $ScriptDir

Write-Host "Installing Antigravity Spotify plugin from: $PluginRoot" -ForegroundColor Cyan

$PythonCmd = $null
if (Get-Command python -ErrorAction SilentlyContinue) {
    $PythonCmd = "python"
} elseif (Get-Command py -ErrorAction SilentlyContinue) {
    $PythonCmd = "py"
} else {
    Write-Error "Python 3 is required on PATH."
    exit 1
}

# Check optional pyaudiowpatch for Windows WASAPI loopback audio capture
$HasWasapi = & $PythonCmd -c "import importlib.util; print('1' if importlib.util.find_spec('pyaudiowpatch') else '0')" 2>$null
if ($HasWasapi -ne "1") {
    Write-Host "Note: Optional 'pyaudiowpatch' is not installed. Track metadata & synced lyrics work out of the box;" -ForegroundColor Yellow
    Write-Host "      to enable 'listen' WASAPI loopback capture on Windows, run: pip install pyaudiowpatch" -ForegroundColor Yellow
}

Write-Host ""
& $PythonCmd "$PluginRoot\scripts\spotify_now.py" doctor
