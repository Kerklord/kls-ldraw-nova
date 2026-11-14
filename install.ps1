# One-command installer for ldraw-nova on Windows.
#
# Double-click Install-LDRAW-NOVA.bat, or open PowerShell and run:
#   irm https://raw.githubusercontent.com/Kerklord/kls-ldraw-nova/master/install.ps1 | iex
# or, from a downloaded copy:
#   powershell -ExecutionPolicy Bypass -File install.ps1
#
# Later: the "Update LDRAW-NOVA" and "Revert LDRAW-NOVA update" shortcuts run this
# script with -Update or -Revert (web app). Terminal mode uses install.sh --update/--revert.
#
# Two ways to use LDRAW-NOVA, and you can install both:
#  - Web app (recommended): a chat page in your browser with a 3D viewer and VR.
#    Runs in Docker Desktop; sign in to Claude or ChatGPT inside the app.
#  - Terminal: Claude Code and/or OpenAI Codex in WSL (Ubuntu inside Windows),
#    driving the ldraw-agent tools directly. Uses your own LDraw library.
# Safe to run again. Works with Windows PowerShell 5.1 and PowerShell 7.

param(
    [string]$Mode = "",          # web | terminal | both
    [string]$AI = "",            # terminal mode: claude | gpt | both
    [string]$Repo = "",          # fork | original | any GitHub URL
    [string]$Dir = "~/ldraw-nova",           # terminal mode: folder inside WSL
    [string]$WebDir = "",        # web mode: Windows folder (default %USERPROFILE%\LDRAW-NOVA)
    [string]$DockerRepo = "",    # web mode: separate web app repository (default: the web-app folder of -Repo)
    [string]$Release = "",       # web mode: release tag such as v0.6.0 (default: the newest one)
    [string]$LDraw = "",         # terminal mode: folder containing parts\ and p\, or complete.zip
    [string]$Distro = "Ubuntu",
    [string]$InstallerRef = "master",
    [switch]$WithLsynth,
    [switch]$NoShortcut,
    [switch]$NoBuild,            # web mode: set everything up but skip the Docker build
    [switch]$Update,             # update an existing install to the latest version (keeps a backup)
    [switch]$Revert,             # switch back to a version saved by -Update
    [switch]$NoSelfUpdate,       # -Update: don't fetch the newest copy of this script first
    [int]$KeepVersions = 3,      # how many previous versions -Update keeps
    [switch]$Yes,
    [switch]$FromBat,            # set by Install-LDRAW-NOVA.bat: report status through the exit code
    [string[]]$InstallArgs = @() # extra options passed to install.sh, e.g. --skip-test
)

$ErrorActionPreference = "Stop"
$DirGiven = $PSBoundParameters.ContainsKey("Dir")
$ScriptParams = $PSBoundParameters
$ForkRepo = "https://github.com/Kerklord/kls-ldraw-nova.git"
$OriginalRepo = "https://github.com/anteloc/ldraw-nova.git"
$OriginalDockerRepo = "https://github.com/anteloc/ldraw-nova-docker"   # only used with the original tools
$OldForkRepo = "https://github.com/Kerklord/ldraw-nova.git"            # this repository before its rename
$InstallerUrl = "https://raw.githubusercontent.com/Kerklord/kls-ldraw-nova/$InstallerRef/install.sh"
$ManagerUrl = "https://raw.githubusercontent.com/Kerklord/kls-ldraw-nova/$InstallerRef/install.ps1"
$ManagerName = "ldraw-nova-manager.ps1"   # copy of this script kept in the web app folder
$ConfigName = "ldraw-nova-install.json"
$ImageName = "ldraw-nova-app"
$WebUrl = "http://localhost:8765"
$DockerBin = Join-Path $env:ProgramFiles "Docker\Docker\resources\bin"
$DockerExe = Join-Path $env:ProgramFiles "Docker\Docker\Docker Desktop.exe"
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
function Confirm-Choice($question) {
    if ($Yes) { return $true }
    $answer = Read-Host "$question (Y/n)"
    return -not ($answer -match '^\s*[nN]')
}
# Opens a Windows folder picker. Returns the folder, $null if cancelled,
# or "" when no window can be shown (then the caller asks in text).
function Select-Folder($description) {
    try {
        Add-Type -AssemblyName System.Windows.Forms
        $dialog = New-Object System.Windows.Forms.FolderBrowserDialog
        $dialog.Description = $description
        $dialog.ShowNewFolderButton = $true
        $owner = New-Object System.Windows.Forms.Form
        $owner.TopMost = $true
        $result = $dialog.ShowDialog($owner)
        $owner.Dispose()
        if ($result -eq [System.Windows.Forms.DialogResult]::OK) { return $dialog.SelectedPath }
        return $null
    } catch {
        return ""
    }
}
function Clean($text) { return (($text | Out-String) -replace "`0", "").Trim() }

# Native commands with redirected stderr must not run under "Stop": Windows
# PowerShell 5.1 turns their stderr lines into terminating errors.
function Get-Distros {
    $ErrorActionPreference = "Continue"
    $raw = & wsl.exe --list --quiet 2>$null
    if ($LASTEXITCODE -ne 0) { return @() }
    return @((Clean $raw) -split "`r?`n" | ForEach-Object { $_.Trim() } | Where-Object { $_ })
}

function Test-WslReady {
    $ErrorActionPreference = "Continue"
    & wsl.exe --status *> $null
    return ($LASTEXITCODE -eq 0)
}

function Test-Admin {
    $id = [Security.Principal.WindowsIdentity]::GetCurrent()
    return (New-Object Security.Principal.WindowsPrincipal($id)).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Invoke-Elevated($command) {
    if (Test-Admin) {
        & powershell.exe -NoProfile -Command $command
    } else {
        Write-Host "  A Windows prompt will ask for permission."
        Start-Process powershell.exe -Verb RunAs -Wait -ArgumentList "-NoProfile", "-Command", $command
    }
}

function Restart-Needed($lines) {
    Write-Host ""
    Write-Host "Almost there. To finish:" -ForegroundColor Yellow
    $n = 1
    foreach ($line in $lines) { Write-Host "  $n. $line"; $n++ }
    Write-Host "  $n. Run this installer again. It continues from where it stopped."
    $script:Status = "restart"
}

function New-Shortcut($name, $target, $arguments, $description) {
    $desktop = [Environment]::GetFolderPath("Desktop")
    $shell = New-Object -ComObject WScript.Shell
    $link = $shell.CreateShortcut((Join-Path $desktop "$name.lnk"))
    $link.TargetPath = $target
    $link.Arguments = $arguments
    $link.Description = $description
    $link.Save()
    Ok "Desktop shortcut '$name': $description"
}

function Test-LDraw($path) {
    if ([string]::IsNullOrWhiteSpace($path)) { return $null }
    try {
        if ((Test-Path -LiteralPath $path -PathType Leaf) -and $path.ToLower().EndsWith(".zip")) { return (Resolve-Path -LiteralPath $path).Path }
        foreach ($candidate in @($path, [IO.Path]::Combine($path, "ldraw"))) {
            if ((Test-Path -LiteralPath ([IO.Path]::Combine($candidate, "parts"))) -and (Test-Path -LiteralPath ([IO.Path]::Combine($candidate, "p")))) {
                return (Resolve-Path -LiteralPath $candidate).Path
            }
        }
    } catch { }
    return $null
}

# ---------- Docker helpers (web mode) ----------
function Add-DockerPath {
    if ((Test-Path -LiteralPath $DockerBin) -and ($env:Path -notlike "*$DockerBin*")) { $env:Path = "$env:Path;$DockerBin" }
}
function Test-DockerRunning {
    $ErrorActionPreference = "Continue"
    if (-not (Get-Command docker -ErrorAction SilentlyContinue)) { return $false }
    & docker info *> $null
    return ($LASTEXITCODE -eq 0)
}
function Wait-Docker($seconds) {
    $deadline = (Get-Date).AddSeconds($seconds)
    while ((Get-Date) -lt $deadline) {
        if (Test-DockerRunning) { Write-Host ""; return $true }
        Write-Host -NoNewline "."
        Start-Sleep -Seconds 3
    }
    Write-Host ""
    return $false
}

function Split-RepoUrl($url) {
    if ($url -notmatch 'github\.com[/:]([^/]+)/([^/]+?)(\.git)?/?$') { Fail "The web app needs a GitHub repository URL, got '$url'." }
    return @($Matches[1], $Matches[2])
}
$GitHubHeaders = @{ "User-Agent" = "ldraw-nova-installer" }

# Looks up the commit of $ref (a tag or branch) in a GitHub repository.
function Get-RepoInfo($url, $ref) {
    $owner, $name = Split-RepoUrl $url
    $commit = Invoke-RestMethod -UseBasicParsing -Uri "https://api.github.com/repos/$owner/$name/commits/$ref" -Headers $GitHubHeaders
    return @{ Url = $url; Owner = $owner; Name = $name; Branch = $ref; Sha = [string]$commit.sha }
}

# Release tags (v1.2.3) of a GitHub repository, newest first.
function Get-ReleaseTags($url) {
    $owner, $name = Split-RepoUrl $url
    # Windows PowerShell 5.1 returns a JSON array as ONE pipeline object, so @(...) around the call
    # would hold the whole array as a single item and "$_.name" would join every tag name into one
    # string. Store it first: piping a variable enumerates its items (a lone object passes as is).
    $tags = Invoke-RestMethod -UseBasicParsing -Uri "https://api.github.com/repos/$owner/$name/tags?per_page=100" -Headers $GitHubHeaders
    $names = @($tags | ForEach-Object { [string]$_.name } | Where-Object { $_ -match '^v\d+(\.\d+){1,3}$' })
    return @($names | Sort-Object { [version]($_.Substring(1)) } -Descending)
}

# The web app and the tools must come from the same release: newer tools code is often
# ahead of the newest web app and breaks it.
#
# Default (no $dockerUrl): your repository ships both. Each release tag (v0.6.1, ...)
# points at a commit with the matching tools and the web app in its web-app folder, so
# one tag always pairs them. Picks $Release, or the newest tag of $novaUrl.
#
# With $dockerUrl (or the original tools): the web app comes from that separate
# repository. Picks the newest release both have; the tools come from $novaUrl when it
# has the tag, otherwise from the original repository.
function Resolve-WebRelease($novaUrl, $dockerUrl) {
    if (-not $dockerUrl -and $novaUrl -ne $OriginalRepo) {
        $tags = @(Get-ReleaseTags $novaUrl)   # @(): a single tag would otherwise come back as a plain string
        if ($Release) {
            if ($tags -notcontains $Release) { Fail "$novaUrl has no release $Release. Available: $($tags -join ', ')" }
            $tag = $Release
        } else {
            $tag = $tags[0]
            if (-not $tag) { Fail "$novaUrl has no release tags yet (such as v0.6.1), so there is no web app version to install." }
        }
        $nova = Get-RepoInfo $novaUrl $tag
        $nova.Chosen = $novaUrl
        return @{ Tag = $tag; Nova = $nova; Docker = @{ Url = ""; Bundled = $true; Branch = $tag; Sha = $nova.Sha } }
    }
    if (-not $dockerUrl) { $dockerUrl = $OriginalDockerRepo }
    $dockerTags = @(Get-ReleaseTags $dockerUrl)
    $originalTags = @(Get-ReleaseTags $OriginalRepo)
    $chosenTags = if ($novaUrl -eq $OriginalRepo) { $originalTags } else { @(Get-ReleaseTags $novaUrl) }
    if ($Release) {
        if ($dockerTags -notcontains $Release) { Fail "The web app has no release $Release. Available: $($dockerTags -join ', ')" }
        $tag = $Release
    } else {
        $tag = @($dockerTags | Where-Object { ($chosenTags -contains $_) -or ($originalTags -contains $_) })[0]
        if (-not $tag) { Fail "Could not find a release that the web app and the tools both have." }
    }
    $source = $novaUrl
    if ($chosenTags -notcontains $tag) {
        if ($originalTags -notcontains $tag) { Fail "The tools have no release $tag." }
        if ($novaUrl -ne $OriginalRepo) { Warn "$novaUrl has no $tag release, so the web app uses the original repository's $tag tools. Terminal mode still uses your repository." }
        $source = $OriginalRepo
    }
    $nova = Get-RepoInfo $source $tag
    $nova.Chosen = $novaUrl
    return @{ Tag = $tag; Nova = $nova; Docker = (Get-RepoInfo $dockerUrl $tag) }
}

# Downloads one exact commit as a zip and unpacks it into $dest (created fresh).
function Save-RepoCode($info, $dest) {
    $short = $info.Sha.Substring(0, 7)
    $zip = Join-Path ([IO.Path]::GetTempPath()) "$($info.Name)-$short.zip"
    $tmp = Join-Path ([IO.Path]::GetTempPath()) "$($info.Name)-$short-unpacked"
    Write-Host "  Downloading $($info.Owner)/$($info.Name) ($($info.Branch) @ $short)"
    $oldProgress = $ProgressPreference; $ProgressPreference = "SilentlyContinue"   # much faster in 5.1
    try {
        Invoke-WebRequest -UseBasicParsing -Uri "https://codeload.github.com/$($info.Owner)/$($info.Name)/zip/$($info.Sha)" -OutFile $zip
        if (Test-Path -LiteralPath $tmp) { Remove-Item -LiteralPath $tmp -Recurse -Force }
        Write-Host "  Unpacking (this can take a few minutes)"
        Expand-Archive -LiteralPath $zip -DestinationPath $tmp -Force
    } finally { $ProgressPreference = $oldProgress }
    $top = @(Get-ChildItem -LiteralPath $tmp -Directory)
    if ($top.Count -ne 1) { Fail "Unexpected layout in the $($info.Name) download." }
    if (Test-Path -LiteralPath $dest) { Remove-Item -LiteralPath $dest -Recurse -Force }
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $dest) | Out-Null
    Move-Item -LiteralPath $top[0].FullName -Destination $dest
    Remove-Item -LiteralPath $tmp -Recurse -Force
    Remove-Item -LiteralPath $zip -Force
    Ok "$($info.Owner)/$($info.Name) $short ready"
}

# Puts the code of $pair (from Resolve-WebRelease) into $dest\ldraw-nova and
# $dest\ldraw-nova-docker, the side-by-side layout docker-compose.yml expects.
function Save-WebCode($pair, $dest) {
    $novaDest = Join-Path $dest "ldraw-nova"
    $dockerDest = Join-Path $dest "ldraw-nova-docker"
    Save-RepoCode $pair.Nova $novaDest
    if (-not $pair.Docker.Bundled) { Save-RepoCode $pair.Docker $dockerDest; return }
    $bundled = Join-Path $novaDest "web-app"
    if (-not (Test-Path -LiteralPath (Join-Path $bundled "docker-compose.yml"))) {
        Fail "Release $($pair.Tag) of $($pair.Nova.Chosen) has no web-app folder. Web app releases are tagged on the release branch (see RELEASES.md)."
    }
    if (Test-Path -LiteralPath $dockerDest) { Remove-Item -LiteralPath $dockerDest -Recurse -Force }
    Move-Item -LiteralPath $bundled -Destination $dockerDest
    Ok "Web app from $($pair.Nova.Name) $($pair.Tag) ready"
}

# Older installs saved the repository's old name and the original author's web app.
# Your repository now ships its own web app, so switch those over.
function Update-SavedRepos($config) {
    if (-not $config) { return }
    if ($config.repo -eq $OldForkRepo -or $config.repo -eq $OldForkRepo.Replace(".git", "")) { $config.repo = $ForkRepo }
    if ($config.repo -ne $OriginalRepo -and $config.dockerRepo -eq $OriginalDockerRepo) {
        $config.dockerRepo = ""
        Ok "The web app now comes from your repository ($($config.repo))"
    }
}

function Read-WebConfig($root) {
    $path = Join-Path $root $ConfigName
    if (Test-Path -LiteralPath $path) { return (Get-Content -LiteralPath $path -Raw | ConvertFrom-Json) }
    return $null
}
function Save-WebConfig($root, $nova, $docker) {
    $data = [ordered]@{
        repo = $nova.Chosen; dockerRepo = $docker.Url; release = $docker.Branch
        novaSha = $nova.Sha; dockerSha = $docker.Sha
        installed = (Get-Date).ToString("yyyy-MM-dd HH:mm")
    }
    $data | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $root $ConfigName) -Encoding UTF8
}

function Test-ImageExists($tag) {
    $ErrorActionPreference = "Continue"
    & docker image inspect $tag *> $null
    return ($LASTEXITCODE -eq 0)
}

function Start-DockerOrFail {
    Add-DockerPath
    if (-not (Get-Command docker -ErrorAction SilentlyContinue)) { Fail "Docker Desktop is not installed. Run the installer first." }
    if (-not (Test-DockerRunning)) {
        Write-Host "  Starting Docker Desktop"
        if (Test-Path -LiteralPath $DockerExe) { Start-Process $DockerExe }
        Write-Host -NoNewline "  Waiting for Docker"
        if (-not (Wait-Docker 300)) { Fail "Docker Desktop did not start. Open it from the Start menu, wait until it is running, and try again." }
    }
}

# Builds the app from code in $codeRoot (folders ldraw-nova and ldraw-nova-docker).
function Build-WebApp($codeRoot) {
    Push-Location (Join-Path $codeRoot "ldraw-nova-docker")
    try {
        & docker compose build | Out-Host
        return ($LASTEXITCODE -eq 0)
    } finally { Pop-Location }
}

function Invoke-Compose($root, [string[]]$composeArgs) {
    Push-Location (Join-Path $root "ldraw-nova-docker")
    try { & docker compose @composeArgs | Out-Host; return ($LASTEXITCODE -eq 0) } finally { Pop-Location }
}
function Remove-Image($tag) {
    $ErrorActionPreference = "Continue"
    & docker image rm $tag *> $null
}

# Swaps the code folders in $root with those in $from; the app's data folder stays in place.
# The replaced code moves to $to. Call with the app stopped.
function Switch-WebCode($root, $from, $to) {
    $liveDocker = Join-Path $root "ldraw-nova-docker"
    $hold = Join-Path $root ".data-hold"
    if (Test-Path -LiteralPath $hold) { Fail "A previous update was interrupted: move '$hold' back to '$liveDocker\data' first." }
    New-Item -ItemType Directory -Force -Path $to | Out-Null
    Move-Item -LiteralPath (Join-Path $liveDocker "data") -Destination $hold
    foreach ($name in @("ldraw-nova", "ldraw-nova-docker")) {
        Move-Item -LiteralPath (Join-Path $root $name) -Destination (Join-Path $to $name)
        Move-Item -LiteralPath (Join-Path $from $name) -Destination (Join-Path $root $name)
    }
    $incomingData = Join-Path $liveDocker "data"
    if (Test-Path -LiteralPath $incomingData) { Remove-Item -LiteralPath $incomingData -Recurse -Force }
    Move-Item -LiteralPath $hold -Destination $incomingData
}

# A version name that is not used yet in $root\versions (two quick operations can share a second).
function New-Stamp($root) {
    $base = Get-Date -Format "yyyyMMdd-HHmmss"
    $stamp = $base; $n = 2
    while ((Test-Path -LiteralPath (Join-Path (Join-Path $root "versions") $stamp)) -or (Test-ImageExists "${ImageName}:v-$stamp")) { $stamp = "$base-$n"; $n++ }
    return $stamp
}

function Get-Backups($root) {
    $dir = Join-Path $root "versions"
    if (-not (Test-Path -LiteralPath $dir)) { return @() }
    return @(Get-ChildItem -LiteralPath $dir -Directory | Sort-Object Name -Descending)
}

function Remove-Backup($backup) {
    Remove-Image "${ImageName}:v-$($backup.Name)"
    Remove-Item -LiteralPath $backup.FullName -Recurse -Force
}

function Update-WebApp($root) {
    Step "Updating the web app in $root"
    $config = Read-WebConfig $root
    if (-not (Test-Path -LiteralPath (Join-Path $root "ldraw-nova-docker"))) { Fail "No web app found in $root. Run the installer first." }
    Update-SavedRepos $config
    $repoUrl = $ForkRepo; $dockerUrl = $DockerRepo
    if ($config) { $repoUrl = $config.repo; $dockerUrl = $config.dockerRepo }
    if ($Repo) { $repoUrl = $Repo }
    if ($DockerRepo) { $dockerUrl = $DockerRepo }
    Start-DockerOrFail
    $pair = Resolve-WebRelease $repoUrl $dockerUrl
    $nova = $pair.Nova; $docker = $pair.Docker
    if ($config -and $config.novaSha -eq $nova.Sha -and $config.dockerSha -eq $docker.Sha) {
        Ok "Already up to date (release $($pair.Tag))"
        return
    }
    $stamp = New-Stamp $root
    $staging = Join-Path $root ".staging"
    if (Test-Path -LiteralPath $staging) { Remove-Item -LiteralPath $staging -Recurse -Force }
    Save-WebCode $pair $staging

    # Keep the running version's image under a version tag, so a revert needs no rebuild.
    if (Test-ImageExists $ImageName) { & docker image tag $ImageName "${ImageName}:v-$stamp" | Out-Null }
    Step "Building the new version (the current one keeps running meanwhile)"
    if (-not (Build-WebApp $staging)) {
        Remove-Item -LiteralPath $staging -Recurse -Force
        if (Test-ImageExists "${ImageName}:v-$stamp") { & docker image tag "${ImageName}:v-$stamp" $ImageName | Out-Null; Remove-Image "${ImageName}:v-$stamp" }
        Fail "The new version did not build, so nothing was changed. Your current version is still installed."
    }

    Step "Switching to the new version"
    Invoke-Compose $root @("stop") | Out-Null
    $backupDir = Join-Path (Join-Path $root "versions") $stamp
    Switch-WebCode $root $staging $backupDir
    if ($config) { $config | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $backupDir $ConfigName) -Encoding UTF8 }
    Remove-Item -LiteralPath $staging -Recurse -Force
    Save-WebConfig $root $nova $docker
    if (-not (Invoke-Compose $root @("up", "-d"))) { Fail "The new version did not start. Use the 'Revert LDRAW-NOVA update' shortcut to go back." }
    Ok "Updated to release $($pair.Tag). The previous version is saved as $stamp."

    $old = @(Get-Backups $root | Select-Object -Skip $KeepVersions)
    foreach ($b in $old) { Remove-Backup $b; Ok "Removed old saved version $($b.Name)" }
}

function Undo-WebUpdate($root) {
    Step "Reverting the web app in $root"
    $backups = @(Get-Backups $root)
    if ($backups.Count -eq 0) { Fail "There is no saved version to go back to yet. A version is saved each time you update." }
    Write-Host "  Saved versions, newest first:"
    $i = 1
    foreach ($b in $backups) {
        $label = $b.Name
        $cfgPath = Join-Path $b.FullName $ConfigName
        if (Test-Path -LiteralPath $cfgPath) {
            $c = Get-Content -LiteralPath $cfgPath -Raw | ConvertFrom-Json
            $rel = if ($c.release) { "release $($c.release)" } else { "tools $($c.novaSha.Substring(0,7)), app $($c.dockerSha.Substring(0,7))" }
            $label = "$($b.Name)  ($rel, installed $($c.installed))"
        }
        Write-Host "  $i) $label"; $i++
    }
    $pick = [int](Ask "Which version?" "1")
    if ($pick -lt 1 -or $pick -gt $backups.Count) { Fail "No such version." }
    $chosen = $backups[$pick - 1]
    Start-DockerOrFail
    $tag = "${ImageName}:v-$($chosen.Name)"
    $haveImage = Test-ImageExists $tag
    if (-not $haveImage) { Warn "The saved image is gone, so this version will be rebuilt." }

    $stamp = New-Stamp $root
    if (Test-ImageExists $ImageName) { & docker image tag $ImageName "${ImageName}:v-$stamp" | Out-Null }
    $config = Read-WebConfig $root
    Invoke-Compose $root @("stop") | Out-Null
    $current = Join-Path (Join-Path $root "versions") $stamp
    Switch-WebCode $root $chosen.FullName $current
    if ($config) { $config | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $current $ConfigName) -Encoding UTF8 }
    $chosenCfg = Join-Path $chosen.FullName $ConfigName
    if (Test-Path -LiteralPath $chosenCfg) { Move-Item -LiteralPath $chosenCfg -Destination (Join-Path $root $ConfigName) -Force }
    Remove-Item -LiteralPath $chosen.FullName -Recurse -Force
    if ($haveImage) {
        & docker image tag $tag $ImageName | Out-Null
        Remove-Image $tag
        $ok = Invoke-Compose $root @("up", "-d", "--no-build")
    } else {
        $ok = (Build-WebApp $root) -and (Invoke-Compose $root @("up", "-d"))
    }
    if (-not $ok) { Fail "The selected version did not start. The version you just left is saved as $stamp; run Revert again to return to it." }
    Ok "Switched back to $($chosen.Name). The version you just left is saved as $stamp, so you can switch again."
}

# Keeps a copy of this script beside the app so the Update and Revert shortcuts work offline.
function Save-Manager($root) {
    $target = Join-Path $root $ManagerName
    if ($PSCommandPath -and (Test-Path -LiteralPath $PSCommandPath)) {
        if ((Resolve-Path -LiteralPath $PSCommandPath).Path -ne $target) { Copy-Item -LiteralPath $PSCommandPath -Destination $target -Force }
    } else {
        Invoke-WebRequest -UseBasicParsing -Uri $ManagerUrl -OutFile $target
    }
}

$StartScript = @'
# Starts the LDRAW-NOVA web app and opens it in your browser.
$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$url = "http://localhost:8765"
$bin = Join-Path $env:ProgramFiles "Docker\Docker\resources\bin"
if ((Test-Path -LiteralPath $bin) -and ($env:Path -notlike "*$bin*")) { $env:Path = "$env:Path;$bin" }
function Test-Docker { $ErrorActionPreference = "Continue"; & docker info *> $null; return ($LASTEXITCODE -eq 0) }
Write-Host "Starting LDRAW-NOVA..."
if (-not (Test-Docker)) {
    Write-Host "Starting Docker Desktop (this can take a minute)"
    Start-Process (Join-Path $env:ProgramFiles "Docker\Docker\Docker Desktop.exe")
    for ($i = 0; $i -lt 100 -and -not (Test-Docker); $i++) { Start-Sleep -Seconds 3 }
}
if (-not (Test-Docker)) { Write-Host "Docker Desktop did not start. Open it from the Start menu, then try again."; Read-Host "Press Enter to close"; exit 1 }
Push-Location (Join-Path $here "ldraw-nova-docker")
try { & docker compose up -d } finally { Pop-Location }
if ($LASTEXITCODE -ne 0) { Read-Host "Starting the app failed. Press Enter to close"; exit 1 }
for ($i = 0; $i -lt 60; $i++) {
    try { Invoke-WebRequest -UseBasicParsing -Uri $url -TimeoutSec 3 | Out-Null; break } catch { Start-Sleep -Seconds 2 }
}
Start-Process $url
'@

$StopScript = @'
# Stops the LDRAW-NOVA web app. Your chats and models are kept.
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$bin = Join-Path $env:ProgramFiles "Docker\Docker\resources\bin"
if ((Test-Path -LiteralPath $bin) -and ($env:Path -notlike "*$bin*")) { $env:Path = "$env:Path;$bin" }
Push-Location (Join-Path $here "ldraw-nova-docker")
try { & docker compose stop } finally { Pop-Location }
Write-Host "LDRAW-NOVA stopped."
Start-Sleep -Seconds 2
'@

# Fetches the newest copy of this script into $root. True when it differs from the
# running one. Offline or on any error, carries on with the running copy.
function Update-Manager($root) {
    $tmp = Join-Path ([IO.Path]::GetTempPath()) "ldraw-nova-manager-new.ps1"
    try {
        Invoke-WebRequest -UseBasicParsing -Uri $ManagerUrl -OutFile $tmp
        if ($PSCommandPath -and (Get-FileHash -LiteralPath $tmp).Hash -eq (Get-FileHash -LiteralPath $PSCommandPath).Hash) { return $false }
        Copy-Item -LiteralPath $tmp -Destination (Join-Path $root $ManagerName) -Force
        Ok "Using the newest installer"
        return $true
    } catch {
        Warn "Could not check for a newer installer ($($_.Exception.Message)); using this one."
        return $false
    } finally { Remove-Item -LiteralPath $tmp -Force -ErrorAction SilentlyContinue }
}

function Invoke-Maintenance {
    $root = $WebDir
    if (-not $root) { $root = Join-Path $env:USERPROFILE "LDRAW-NOVA" }
    if (-not (Test-Path -LiteralPath $root)) { Fail "No LDRAW-NOVA web app found in $root." }
    $root = (Resolve-Path -LiteralPath $root).Path
    if ($Update -and -not $NoSelfUpdate -and (Update-Manager $root)) {
        # Run the newest installer for this update, so installer fixes reach the Update shortcut.
        $passed = @("-Update", "-WebDir", $root, "-NoSelfUpdate")
        foreach ($k in @("Repo", "DockerRepo", "Release")) { if ($ScriptParams.ContainsKey($k)) { $passed += @("-$k", (Get-Variable $k -ValueOnly)) } }
        foreach ($k in @("Yes", "FromBat")) { if ($ScriptParams.ContainsKey($k)) { $passed += "-$k" } }
        & powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $root $ManagerName) @passed
        exit $LASTEXITCODE
    }
    try {
        if ($Update) { Update-WebApp $root } else { Undo-WebUpdate $root }
    } catch {
        # Show the reason before "Press Enter to close"; otherwise it only appears as the window closes.
        Write-Host ""
        Write-Host "Error: $($_.Exception.Message)" -ForegroundColor Red
        if ($Yes -or $FromBat) { throw }
        $script:MaintenanceFailed = $true
    } finally {
        if (-not $Yes -and -not $FromBat) { Read-Host "Press Enter to close" | Out-Null }
    }
    if ($script:MaintenanceFailed) { exit 1 }
}

function Main {
if ($Update -or $Revert) { Invoke-Maintenance; return }
Write-Host "LDRAW-NOVA installer for Windows" -ForegroundColor White

# ---------- 1. How will you use it? ----------
if (-not $Mode) {
    Step "How do you want to use LDRAW-NOVA?"
    Write-Host "  1) Web app in your browser (recommended): chat, 3D viewer and VR; sign in to Claude or ChatGPT inside"
    Write-Host "  2) Terminal: Claude Code or GPT (Codex) in a command window, using your own LDraw library"
    Write-Host "  3) Both"
    switch (Ask "Choose 1, 2 or 3" "1") { "2" { $Mode = "terminal" } "3" { $Mode = "both" } default { $Mode = "web" } }
}
if (@("web", "terminal", "both") -notcontains $Mode) { Fail "-Mode must be web, terminal or both." }
$wantWeb = ($Mode -ne "terminal")
$wantTerminal = ($Mode -ne "web")
Ok "Installing: $(if ($Mode -eq 'both') { 'web app and terminal' } else { "$Mode" })"

# ---------- 2. WSL ----------
Step "Checking WSL"
if (-not (Get-Command wsl.exe -ErrorAction SilentlyContinue)) {
    Fail "wsl.exe was not found. LDRAW-NOVA needs Windows 10 version 2004 or newer, or Windows 11."
}
if ($wantTerminal) {
    $distros = Get-Distros
    if ($distros -notcontains $Distro) {
        Warn "$Distro is not installed in WSL yet. Installing it needs administrator rights."
        Invoke-Elevated "wsl.exe --install -d $Distro"
        $distros = Get-Distros
        if ($distros -notcontains $Distro) {
            Restart-Needed @("Restart your PC if Windows asks you to.",
                             "Open '$Distro' from the Start menu once, and create a Linux username and password.")
            return
        }
    }
    $linuxUser = Clean (& wsl.exe -d $Distro -e sh -c 'id -un')
    if ([string]::IsNullOrWhiteSpace($linuxUser)) {
        Fail "Could not start $Distro. Open it from the Start menu once to finish its setup, then run this again."
    }
    if ($linuxUser -eq "root") { Warn "$Distro logs in as root. That works, but a normal user is recommended." }
    Ok "$Distro is ready (Linux user: $linuxUser)"
    $linuxHome = Clean (& wsl.exe -d $Distro -e sh -c 'echo $HOME')
} else {
    if (-not (Test-WslReady)) {
        Warn "WSL is not set up yet. Docker Desktop needs it. Installing it needs administrator rights."
        Invoke-Elevated "wsl.exe --install --no-distribution"
        Restart-Needed @("Restart your PC.")
        return
    }
    Ok "WSL is available"
}

# ---------- 3. Repository ----------
Step "Choosing the repository"
if (-not $Repo) {
    Write-Host "  1) Your fork:    $ForkRepo"
    Write-Host "  2) The original: $OriginalRepo"
    Write-Host "  Or paste any other GitHub URL."
    $Repo = Ask "Which repository?" "1"
}
switch ($Repo) {
    "1" { $Repo = $ForkRepo }
    "fork" { $Repo = $ForkRepo }
    "2" { $Repo = $OriginalRepo }
    "original" { $Repo = $OriginalRepo }
}
Ok "Repository: $Repo"

# ---------- 4. Web app ----------
$webRoot = $null
if ($wantWeb) {
    Step "Checking Docker Desktop (runs the web app)"
    Add-DockerPath
    if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
        if (Get-Command winget -ErrorAction SilentlyContinue) {
            Write-Host "  Installing Docker Desktop with winget (a Windows prompt may ask for permission)"
            & winget install -e --id Docker.DockerDesktop --accept-source-agreements --accept-package-agreements
            Add-DockerPath
        }
        if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
            Start-Process "https://www.docker.com/products/docker-desktop/"
            Restart-Needed @("Install Docker Desktop from the page that just opened.", "Restart your PC if it asks you to.")
            return
        }
        Ok "Docker Desktop installed"
    }
    if (-not (Test-DockerRunning)) {
        Write-Host "  Starting Docker Desktop. If it shows its terms or a sign-in screen, accept the terms (signing in is optional)."
        if (Test-Path -LiteralPath $DockerExe) { Start-Process $DockerExe }
        Write-Host -NoNewline "  Waiting for Docker"
        if (-not (Wait-Docker 300)) {
            Restart-Needed @("Open Docker Desktop from the Start menu and wait until it says it is running.",
                             "If it asks you to sign out or restart, do that first.")
            return
        }
    }
    Ok "Docker is running"

    Step "Choosing where to put the web app"
    $webRoot = $WebDir
    if (-not $webRoot) {
        $webRoot = Join-Path $env:USERPROFILE "LDRAW-NOVA"
        if (-not (Confirm-Choice "Install the web app in ${webRoot}?")) {
            Write-Host "  A window will open. Pick where the 'LDRAW-NOVA' folder should be created."
            $parent = Select-Folder "Pick where to create the 'LDRAW-NOVA' folder"
            if ($null -eq $parent) { Fail "No folder selected." }
            if ($parent -eq "") { $parent = Ask "Folder to create LDRAW-NOVA in" $env:USERPROFILE }
            $webRoot = Join-Path $parent "LDRAW-NOVA"
        }
    }
    New-Item -ItemType Directory -Force -Path $webRoot | Out-Null
    $webRoot = (Resolve-Path -LiteralPath $webRoot).Path
    Ok "Folder: $webRoot"

    Step "Downloading the web app and the tools"
    # docker-compose.yml expects the two folders side by side.
    $pair = Resolve-WebRelease $Repo $DockerRepo
    $novaInfo = $pair.Nova; $dockerInfo = $pair.Docker
    Ok "Release $($pair.Tag) of the web app and the tools"
    $existingData = Join-Path $webRoot "ldraw-nova-docker\data"
    $keepData = Join-Path $webRoot ".data-hold"
    if (Test-Path -LiteralPath $existingData) { Move-Item -LiteralPath $existingData -Destination $keepData }
    Save-WebCode $pair $webRoot
    if (Test-Path -LiteralPath $keepData) {
        Remove-Item -LiteralPath $existingData -Recurse -Force
        Move-Item -LiteralPath $keepData -Destination $existingData
        Ok "Kept your existing chats and models"
    }
    Save-WebConfig $webRoot $novaInfo $dockerInfo
    Save-Manager $webRoot
    Set-Content -LiteralPath (Join-Path $webRoot "Start LDRAW-NOVA.ps1") -Value $StartScript -Encoding UTF8
    Set-Content -LiteralPath (Join-Path $webRoot "Stop LDRAW-NOVA.ps1") -Value $StopScript -Encoding UTF8

    if ($NoBuild) {
        Warn "Skipped the Docker build (-NoBuild). The first start will build it."
    } else {
        Step "Building the web app (first time: 15 to 40 minutes and about 5 GB of downloads)"
        Push-Location (Join-Path $webRoot "ldraw-nova-docker")
        try {
            & docker compose build
            if ($LASTEXITCODE -ne 0) { Fail "The Docker build failed. Read the messages above; running this installer again retries it." }
            & docker compose up -d
            if ($LASTEXITCODE -ne 0) { Fail "The web app did not start. Read the messages above." }
        } finally { Pop-Location }
        Ok "The web app is running at $WebUrl"
    }
}

# ---------- 5. Terminal ----------
$terminalDir = $null
if ($wantTerminal) {
    Step "Locating your LDraw parts library (terminal mode uses your own copy)"
    $found = $null
    if ($LDraw) {
        $found = Test-LDraw $LDraw
    } else {
        $guesses = @("C:\LDraw", "C:\ldraw", "$env:USERPROFILE\LDraw", "$env:USERPROFILE\Documents\LDraw",
                     "$env:USERPROFILE\Downloads\ldraw", "$env:USERPROFILE\Downloads\complete.zip",
                     "${env:ProgramFiles}\LDraw", "${env:ProgramFiles(x86)}\LDraw")
        $default = ""
        foreach ($g in $guesses) { if (Test-LDraw $g) { $default = $g; break } }
        if ($default) {
            if ($Yes -or (Confirm-Choice "Found your LDraw library at $default. Use it?")) { $found = Test-LDraw $default }
        }
        while (-not $found) {
            if ($Yes) { break }
            Write-Host "  A window will open. Select your LDraw folder (the one that contains 'parts' and 'p')."
            $answer = Select-Folder "Select your LDraw folder (it contains the 'parts' and 'p' folders)"
            if ($null -eq $answer) { Fail "No LDraw folder selected." }
            if ($answer -eq "") { $answer = Ask "Folder with your LDraw parts (contains parts and p), or complete.zip" $default }
            $found = Test-LDraw $answer
            if (-not $found) { Warn "No 'parts' and 'p' folders in '$answer'. Please pick the LDraw folder itself." }
        }
    }
    if (-not $found) { Fail "Could not find the LDraw library. Pass -LDraw 'C:\path\to\ldraw'." }
    $ldrawLinux = Clean (& wsl.exe -d $Distro -e wslpath -a "$found")
    if (-not $ldrawLinux.StartsWith("/")) { Fail "Could not translate '$found' into a WSL path." }
    Ok "Library: $found (in WSL: $ldrawLinux)"

    if (-not $AI) {
        Step "Choosing the AI for the terminal"
        Write-Host "  1) Claude (Claude Code; needs a Claude plan or an Anthropic API key)"
        Write-Host "  2) GPT (OpenAI Codex; needs a ChatGPT plan or an OpenAI API key)"
        Write-Host "  3) Both"
        switch (Ask "Choose 1, 2 or 3" "1") { "2" { $AI = "gpt" } "3" { $AI = "both" } default { $AI = "claude" } }
    }
    if (@("claude", "gpt", "both") -notcontains $AI) { Fail "-AI must be claude, gpt or both." }

    $dirLinux = $null
    if (-not $DirGiven -and -not $Yes) {
        Step "Choosing where to install the terminal tools"
        Write-Host "  1) Recommended: inside Linux (fastest). The 'LDRAW-NOVA files' shortcut opens it."
        Write-Host "  2) Pick a folder on this PC (easier to find, but builds run noticeably slower)"
        if ((Ask "Choose 1 or 2" "1") -eq "2") {
            Write-Host "  A window will open. Pick where the 'ldraw-nova' folder should be created."
            $parent = Select-Folder "Pick where to create the 'ldraw-nova' folder"
            if ($null -eq $parent) { Fail "No install folder selected." }
            if ($parent -eq "") { $parent = Ask "Folder to create ldraw-nova in" "$env:USERPROFILE" }
            if (-not (Test-Path -LiteralPath $parent -PathType Container)) { Fail "Folder not found: $parent" }
            $parentLinux = Clean (& wsl.exe -d $Distro -e wslpath -a "$parent")
            if (-not $parentLinux.StartsWith("/")) { Fail "Could not translate '$parent' into a WSL path." }
            $dirLinux = $parentLinux.TrimEnd("/") + "/ldraw-nova"
        }
    }
    if (-not $dirLinux) {
        $dirLinux = $Dir
        if ($dirLinux -eq "~") { $dirLinux = $linuxHome }
        elseif ($dirLinux.StartsWith("~/")) { $dirLinux = $linuxHome + $dirLinux.Substring(1) }
    }
    Ok "Folder: $dirLinux (path inside $Distro)"

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
    $shArgs = @("--repo", $Repo, "--dir", $dirLinux, "--ldraw", $ldrawLinux, "--copy-ldraw", "--ai", $AI)
    if ($WithLsynth) { $shArgs += "--with-lsynth" }
    if ($Yes) { $shArgs += "--yes" }
    $shArgs += $InstallArgs
    # Plain arguments only: Windows PowerShell 5.1 mangles embedded double quotes.
    & wsl.exe -d $Distro -e bash $scriptLinux @shArgs
    $code = $LASTEXITCODE
    if ($code -ne 0 -and $code -ne 3) { Fail "The Linux installer stopped (exit code $code). Read the messages above, fix the problem, and run this again." }
    if ($code -eq 3) { Warn "The terminal tools are installed, but their test build did not pass. See the messages above." }
    $terminalDir = Clean (& wsl.exe -d $Distro -e wslpath -w "$dirLinux")
}

# ---------- 6. Shortcuts ----------
if (-not $NoShortcut) {
    Step "Creating desktop shortcuts"
    $ps = "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe"
    $wsl = "$env:SystemRoot\System32\wsl.exe"
    if ($wantWeb) {
        New-Shortcut "LDRAW-NOVA" $ps "-NoProfile -ExecutionPolicy Bypass -File `"$webRoot\Start LDRAW-NOVA.ps1`"" "Start the web app and open it in your browser"
        New-Shortcut "Stop LDRAW-NOVA" $ps "-NoProfile -ExecutionPolicy Bypass -File `"$webRoot\Stop LDRAW-NOVA.ps1`"" "Stop the web app (chats and models are kept)"
        New-Shortcut "LDRAW-NOVA models" "$env:SystemRoot\explorer.exe" "`"$webRoot\ldraw-nova-docker\data`"" "Open the folder with your models and chats"
        New-Shortcut "Update LDRAW-NOVA" $ps "-NoProfile -ExecutionPolicy Bypass -File `"$webRoot\$ManagerName`" -Update -WebDir `"$webRoot`"" "Update the web app to the latest version (the current one is saved)"
        New-Shortcut "Revert LDRAW-NOVA update" $ps "-NoProfile -ExecutionPolicy Bypass -File `"$webRoot\$ManagerName`" -Revert -WebDir `"$webRoot`"" "Switch the web app back to a saved version"
    }
    if ($wantTerminal) {
        if ($AI -eq "claude" -or $AI -eq "both") {
            New-Shortcut "LDRAW-NOVA Claude (terminal)" $wsl "-d $Distro --cd `"$dirLinux`" -e bash -lic `"claude; exec bash`"" "Open Claude Code in ldraw-nova"
        }
        if ($AI -eq "gpt" -or $AI -eq "both") {
            New-Shortcut "LDRAW-NOVA GPT (terminal)" $wsl "-d $Distro --cd `"$dirLinux`" -e bash -lic `"codex; exec bash`"" "Open OpenAI Codex in ldraw-nova"
        }
        if ($terminalDir) {
            New-Shortcut "LDRAW-NOVA files" "$env:SystemRoot\explorer.exe" "`"$terminalDir`"" "Open the terminal tools folder (models land in output)"
        }
        New-Shortcut "Update LDRAW-NOVA (terminal)" $wsl "-d $Distro --cd `"$dirLinux`" -e bash -lc `"bash ~/.local/share/ldraw-nova/install.sh --update --dir '$dirLinux'; read -rp 'Press Enter to close' _`"" "Update the terminal tools (the current version is remembered)"
        New-Shortcut "Revert LDRAW-NOVA (terminal)" $wsl "-d $Distro --cd `"$dirLinux`" -e bash -lc `"bash ~/.local/share/ldraw-nova/install.sh --revert --dir '$dirLinux'; read -rp 'Press Enter to close' _`"" "Go back to the terminal tools version before the last update"
    }
}

# ---------- Done ----------
Step "Done"
if ($wantWeb) {
    Write-Host "  Web app: double-click 'LDRAW-NOVA' on your desktop (or open $WebUrl)."
    Write-Host "  First time: open Settings in the app, sign in to Claude or ChatGPT, and add an agent."
    if (-not $NoBuild -and -not $Yes) { Start-Process $WebUrl }
}
if ($wantTerminal) {
    Write-Host "  Terminal: double-click 'LDRAW-NOVA Claude (terminal)' or 'LDRAW-NOVA GPT (terminal)',"
    Write-Host "  then describe the model you want. Models appear in: $terminalDir\output"
}
}

$script:Status = "ok"
if ($FromBat) {
    try { Main } catch { Write-Host ""; Write-Host "Error: $($_.Exception.Message)" -ForegroundColor Red; exit 1 }
    if ($script:Status -eq "restart") { exit 2 }
    exit 0
}
Main
