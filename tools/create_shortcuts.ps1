# Creates "Puzzle Answer Assistant" shortcuts (Desktop + Start menu) with the
# app icon. Pin either one to the taskbar: right-click > Pin to taskbar.
$ErrorActionPreference = "Stop"
$app = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$pyw = Join-Path $app ".venv\Scripts\pythonw.exe"
if (-not (Test-Path $pyw)) { Write-Host "Run INSTALL.bat first."; exit 1 }
$shell = New-Object -ComObject WScript.Shell
$targets = @([Environment]::GetFolderPath("Desktop"), [Environment]::GetFolderPath("Programs"))
foreach ($dir in $targets) {
    $lnk = $shell.CreateShortcut((Join-Path $dir "Puzzle Answer Assistant.lnk"))
    $lnk.TargetPath = $pyw
    $lnk.Arguments = '"' + (Join-Path $app "app.py") + '"'
    $lnk.WorkingDirectory = $app
    $lnk.IconLocation = (Join-Path $app "assets\app.ico") + ",0"
    $lnk.Description = "Read-only answer assistant for the Montar un arma puzzle"
    $lnk.Save()
    Write-Host "Shortcut created: $($lnk.FullName)"
}
