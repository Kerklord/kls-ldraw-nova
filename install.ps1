# One-command installer for ldraw-nova on Windows.
#
# Open PowerShell and run:
#   irm https://raw.githubusercontent.com/Kerklord/ldraw-nova/master/install.ps1 | iex
# or, from a downloaded copy:
#   powershell -ExecutionPolicy Bypass -File install.ps1
#
# ldraw-nova's tools are Linux shell scripts, so this sets up WSL (Ubuntu inside
# Windows), then runs install.sh there. Your LDraw library can stay on C:\; the
# installer copies it into Linux for speed. Safe to run again.
# Works with Windows PowerShell 5.1 and PowerShell 7.

param(
    [string]$Repo = "",          # fork | original | any git URL
    [string]$Dir = "~/ldraw-nova",
    [string]$LDraw = "",         # Windows folder containing parts\ and p\, or complete.zip
    [string]$Distro = "Ubuntu",
    [string]$InstallerRef = "master",
    [switch]$WithLsynth,
    [switch]$NoShortcut,
    [switch]$Yes,
    [string[]]$InstallArgs = @()  # extra options passed to install.sh, e.g. --skip-test
)

$ErrorActionPreference = "Stop"
$DirGiven = $PSBoundParameters.ContainsKey("Dir")
$ForkRepo = "https://github.com/Kerklord/ldraw-nova.git"
$OriginalRepo = "https://github.com/anteloc/ldraw-nova.git"
$InstallerUrl = "https://raw.githubusercontent.com/Kerklord/ldraw-nova/$InstallerRef/install.sh"
$env:WSL_UTF8 = "1"   # make wsl.exe print UTF-8 instead of UTF-16

function Step($text) { Write-Host ""; Write-Host "==> $text" -ForegroundColor Cyan }
function Ok($text)   { Write-Host "  [ok] $text" -ForegroundColor Green }
function Warn($text) { Write-Host "  [!] $text" -ForegroundColor Yellow }
function Fail($text) { throw "ldraw-nova installer: $text" }
function Ask($question, $default) {
    if ($Yes) { return $default }
    $answer = Read-Host "$question [$default]"
    if ([string]::IsNullOrWhiteSpace($answer)) { return $default }
    return $answer.Trim().Trim('"')
}
function Clean($text) { return (($text | Out-String) -replace "`0", "").Trim() }

function Get-Distros {
    $raw = & wsl.exe --list --quiet 2>$null
    if ($LASTEXITCODE -ne 0) { return @() }
    return @((Clean $raw) -split "`r?`n" | ForEach-Object { $_.Trim() } | Where-Object { $_ })
}

function Test-Admin {
    $id = [Security.Principal.WindowsIdentity]::GetCurrent()
    return (New-Object Security.Principal.WindowsPrincipal($id)).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Main {
Write-Host "ldraw-nova installer for Windows" -ForegroundColor White

# ---------- 1. WSL and Ubuntu ----------
Step "Checking WSL and $Distro"
if (-not (Get-Command wsl.exe -ErrorAction SilentlyContinue)) {
    Fail "wsl.exe was not found. WSL needs Windows 10 version 2004 or newer, or Windows 11."
}
$distros = Get-Distros
if ($distros -notcontains $Distro) {
    Warn "$Distro is not installed in WSL yet. Installing it needs administrator rights."
    $cmd = "wsl.exe --install -d $Distro"
    if (Test-Admin) {
        & wsl.exe --install -d $Distro
    } else {
        Write-Host "  A Windows prompt will ask for permission."
        Start-Process powershell.exe -Verb RunAs -Wait -ArgumentList "-NoProfile", "-Command", $cmd
    }
    $distros = Get-Distros
    if ($distros -notcontains $Distro) {
        Write-Host ""
        Write-Host "WSL is being set up. To finish:" -ForegroundColor Yellow
        Write-Host "  1. Restart your PC if Windows asks you to."
        Write-Host "  2. Open '$Distro' from the Start menu once, and create a Linux username and password."
        Write-Host "  3. Run this installer again. It will continue from here."
        return
    }
}
$linuxUser = Clean (& wsl.exe -d $Distro -e sh -c 'id -un')
if ([string]::IsNullOrWhiteSpace($linuxUser)) {
    Fail "Could not start $Distro. Open it from the Start menu once to finish its setup, then run this again."
}
if ($linuxUser -eq "root") {
    Warn "$Distro logs in as root. That works, but a normal user is recommended."
}
Ok "$Distro is ready (Linux user: $linuxUser)"
$linuxHome = Clean (& wsl.exe -d $Distro -e sh -c 'echo $HOME')

# ---------- 2. LDraw library ----------
Step "Locating your LDraw parts library"
function Test-LDraw($path) {
    if ([string]::IsNullOrWhiteSpace($path)) { return $null }
    if ((Test-Path -LiteralPath $path -PathType Leaf) -and $path.ToLower().EndsWith(".zip")) { return (Resolve-Path -LiteralPath $path).Path }
    foreach ($candidate in @($path, (Join-Path $path "ldraw"))) {
        if ((Test-Path -LiteralPath (Join-Path $candidate "parts")) -and (Test-Path -LiteralPath (Join-Path $candidate "p"))) {
            return (Resolve-Path -LiteralPath $candidate).Path
        }
    }
    return $null
}
$found = $null
if ($LDraw) {
    $found = Test-LDraw $LDraw
} else {
    $guesses = @("C:\LDraw", "C:\ldraw", "$env:USERPROFILE\LDraw", "$env:USERPROFILE\Documents\LDraw",
                 "$env:USERPROFILE\Downloads\ldraw", "$env:USERPROFILE\Downloads\complete.zip",
                 "${env:ProgramFiles}\LDraw", "${env:ProgramFiles(x86)}\LDraw")
    $default = ""
    foreach ($g in $guesses) { if (Test-LDraw $g) { $default = $g; break } }
    while (-not $found) {
        $answer = Ask "Folder with your LDraw parts (contains parts and p), or complete.zip" $default
        $found = Test-LDraw $answer
        if (-not $found) {
            if ($Yes) { break }
            Warn "No parts and p folders found in '$answer'. Try again."
        }
    }
}
if (-not $found) { Fail "Could not find the LDraw library. Pass -LDraw 'C:\path\to\ldraw'." }
$ldrawLinux = Clean (& wsl.exe -d $Distro -e wslpath -a "$found")
if (-not $ldrawLinux.StartsWith("/")) { Fail "Could not translate '$found' into a WSL path." }
Ok "Library: $found (in WSL: $ldrawLinux)"

# ---------- 3. Repository choice ----------
Step "Choosing the repository"
if (-not $Repo) {
    Write-Host "  1) Your fork:    $ForkRepo"
    Write-Host "  2) The original: $OriginalRepo"
    Write-Host "  Or paste any other git URL."
    $Repo = Ask "Which repository?" "1"
}
switch ($Repo) {
    "1" { $Repo = $ForkRepo }
    "fork" { $Repo = $ForkRepo }
    "2" { $Repo = $OriginalRepo }
    "original" { $Repo = $OriginalRepo }
}
if (-not $DirGiven -and -not $Yes) {
    $Dir = Ask "Install folder inside WSL" $Dir
}
Ok "Repository: $Repo"
Ok "Folder: $Dir (inside $Distro)"

# ---------- 4. Run install.sh inside WSL ----------
Step "Installing inside $Distro (it may ask for your Linux password)"
$local = $null
if ($PSScriptRoot) { $local = Join-Path $PSScriptRoot "install.sh" }
if ($local -and (Test-Path -LiteralPath $local)) {
    $script = (Resolve-Path -LiteralPath $local).Path
    Ok "Using $script"
} else {
    $script = Join-Path ([IO.Path]::GetTempPath()) "ldraw-nova-install.sh"
    Invoke-WebRequest -UseBasicParsing -Uri $InstallerUrl -OutFile $script
    Ok "Downloaded install.sh"
}
$scriptLinux = Clean (& wsl.exe -d $Distro -e wslpath -a "$script")
$dirLinux = $Dir
if ($dirLinux -eq "~") { $dirLinux = $linuxHome }
elseif ($dirLinux.StartsWith("~/")) { $dirLinux = $linuxHome + $dirLinux.Substring(1) }
$shArgs = @("--repo", $Repo, "--dir", $dirLinux, "--ldraw", $ldrawLinux, "--copy-ldraw")
if ($WithLsynth) { $shArgs += "--with-lsynth" }
if ($Yes) { $shArgs += "--yes" }
$shArgs += $InstallArgs
# Plain arguments only: Windows PowerShell 5.1 mangles embedded double quotes.
& wsl.exe -d $Distro -e bash $scriptLinux @shArgs
$code = $LASTEXITCODE
if ($code -ne 0 -and $code -ne 3) { Fail "The Linux installer stopped (exit code $code). Read the messages above, fix the problem, and run this again." }

# ---------- 5. Shortcuts ----------
$dirWindows = Clean (& wsl.exe -d $Distro -e wslpath -w "$dirLinux")

if (-not $NoShortcut) {
    Step "Creating desktop shortcuts"
    $desktop = [Environment]::GetFolderPath("Desktop")
    $shell = New-Object -ComObject WScript.Shell
    $start = $shell.CreateShortcut((Join-Path $desktop "LDRAW-NOVA.lnk"))
    $start.TargetPath = "$env:SystemRoot\System32\wsl.exe"
    $start.Arguments = "-d $Distro --cd `"$dirLinux`" -e bash -lic `"claude; exec bash`""
    $start.Description = "Open Claude Code in ldraw-nova"
    $start.Save()
    Ok "LDRAW-NOVA: opens Claude Code in the repository"
    if ($dirWindows) {
        $files = $shell.CreateShortcut((Join-Path $desktop "LDRAW-NOVA files.lnk"))
        $files.TargetPath = "$env:SystemRoot\explorer.exe"
        $files.Arguments = "`"$dirWindows`""
        $files.Save()
        Ok "LDRAW-NOVA files: opens the repository in Explorer (models land in output\)"
    }
}

Step "Done"
if ($code -eq 3) { Warn "Everything is installed, but the test build did not pass. See the messages above." }
Write-Host "  Double-click 'LDRAW-NOVA' on your desktop, or run:  wsl -d $Distro --cd $dirLinux"
Write-Host "  then type 'claude' and describe the model you want."
if ($dirWindows) { Write-Host "  Your models and previews appear in: $dirWindows\output" }
}

Main
