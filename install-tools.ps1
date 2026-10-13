<#
.SYNOPSIS
Install the external tools stegray drives, on Windows.

.DESCRIPTION
The Windows counterpart of install-tools.sh. Packaged tools come from Scoop;
the rest are downloaded (pinned and checksummed) into PREFIX, whose bin and
steghide folders are added to your user PATH. Tools already on PATH are left
alone, so it is safe to run again.

Several tools have no native Windows build (see $Unavailable below). For the
full set, run install-tools.sh inside WSL or use the container image.

.EXAMPLE
powershell -ExecutionPolicy Bypass -File install-tools.ps1 -DryRun
#>
[CmdletBinding()]
param(
    # Where tools without a Scoop package go.
    [string]$Prefix = (Join-Path $env:LOCALAPPDATA 'stegray-tools'),
    # Install Scoop without asking when it is missing.
    [switch]$InstallScoop,
    # Print what would be done without doing it.
    [switch]$DryRun
)

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

$OpenStegoVersion = '0.8.6'
$OpenStegoSha256 = '402ba4190d329564996a1250b9096f0402452042469f517545c312527cedc41f'
$JstegVersion = '0.3.0'
$JstegSha256 = '9635c0059d9fc876cca58f7768702e0625ba0988cfa07e28c1bdef121b7c3578'
$SteghideVersion = '0.5.1'
$SteghideSha256 = 'd8211b43dc4dad1333c9ac8b60f5a468f5f8d744f80f9571df431def925cd579'

# Command -> Scoop package providing it.
$ScoopPackages = [ordered]@{
    file     = 'file'
    strings  = 'binutils'
    exiftool = 'exiftool'
    identify = 'imagemagick'
    pdfinfo  = 'poppler'
    java     = 'java/temurin-jre'
}
# Installed by the functions further down.
$Extras = 'pdfid', 'steghide', 'jsteg', 'openstego', 'zsteg'
# No native Windows build; their analyzers are skipped.
$Unavailable = 'binwalk', 'foremost', 'pngcheck', 'outguess', 'jpseek'

$BinDir = Join-Path $Prefix 'bin'
$RepoDir = $PSScriptRoot

function Write-Info([string]$Message) { Write-Host "==> $Message" -ForegroundColor White }

function Test-Tool([string]$Name) {
    [bool](Get-Command $Name -CommandType Application -ErrorAction SilentlyContinue)
}

function Invoke-Tool([string]$Exe, [string[]]$Arguments) {
    if ($DryRun) {
        Write-Host "    $Exe $Arguments"
        return
    }
    $global:LASTEXITCODE = 0
    & $Exe @Arguments
    if ($LASTEXITCODE -ne 0) { throw "$Exe $Arguments failed (exit code $LASTEXITCODE)" }
}

# Pick up PATH entries added by the installs so far (Scoop shims, Ruby, ...).
function Update-SessionPath {
    $paths = @($BinDir, (Join-Path $Prefix 'steghide'))
    foreach ($scope in 'User', 'Machine') {
        $paths += [Environment]::GetEnvironmentVariable('Path', $scope) -split ';'
    }
    $env:Path = (($paths + ($env:Path -split ';')) | Where-Object { $_ } | Select-Object -Unique) -join ';'
}

function Add-UserPath([string]$Dir) {
    $current = [Environment]::GetEnvironmentVariable('Path', 'User')
    if (($current -split ';') -contains $Dir) { return }
    Write-Info "adding $Dir to your user PATH"
    [Environment]::SetEnvironmentVariable('Path', (@($current, $Dir) | Where-Object { $_ }) -join ';', 'User')
}

function Get-Download([string]$Url, [string]$Dest, [string]$Sha256) {
    Invoke-Tool 'curl.exe' @('-fsSL', '-o', $Dest, $Url)
    $actual = (Get-FileHash -Algorithm SHA256 -LiteralPath $Dest).Hash
    if ($actual -ne $Sha256) { throw "checksum mismatch for $Url (got $actual)" }
}

# stegray resolves tools through PATHEXT, so a .cmd wrapper is enough.
function Set-Shim([string]$Name, [string]$CommandLine) {
    Set-Content -LiteralPath (Join-Path $BinDir "$Name.cmd") -Encoding Ascii -Value "@echo off`r`n$CommandLine %*"
}

# ---------------------------------------------------------------------------
# Scoop
# ---------------------------------------------------------------------------

function Install-Scoop {
    if ($script:ScoopReady -or (Test-Tool 'scoop')) { return }
    Write-Info 'Scoop (https://scoop.sh) is not installed'
    if ($DryRun) {
        Write-Host '    Invoke-RestMethod https://get.scoop.sh | Invoke-Expression'
        $script:ScoopReady = $true
        return
    }
    if (-not $InstallScoop) {
        $answer = Read-Host 'Install it now for the current user? [y/N]'
        if ($answer -notmatch '^[yY]') { throw 'Scoop is required; install it and run this script again' }
    }
    Invoke-RestMethod -Uri 'https://get.scoop.sh' | Invoke-Expression
    Update-SessionPath
    if (-not (Test-Tool 'scoop')) { throw 'Scoop was installed but is not on PATH; open a new terminal and run this script again' }
}

function Install-ScoopPackage([string]$Package) {
    Install-Scoop
    if ($Package -like '*/*') {
        # The bucket needs git; adding one that is already there is harmless.
        if (-not (Test-Tool 'git')) { Invoke-Tool 'scoop' @('install', 'git') }
        try { Invoke-Tool 'scoop' @('bucket', 'add', ($Package -split '/')[0]) } catch { }
    }
    Write-Info "installing Scoop package: $Package"
    Invoke-Tool 'scoop' @('install', $Package)
    if (-not $DryRun) { Update-SessionPath }
}

# ---------------------------------------------------------------------------
# Tools Scoop does not carry
# ---------------------------------------------------------------------------

function Install-Pdfid {
    # Didier Stevens' pdfid.py, vendored in docker/ (see the Dockerfile).
    $lib = Join-Path $Prefix 'lib'
    New-Item -ItemType Directory -Force -Path $lib | Out-Null
    Copy-Item -LiteralPath (Join-Path $RepoDir 'docker\pdfid.py') -Destination (Join-Path $lib 'pdfid.py') -Force
    $python = 'python'
    if (Test-Tool 'py') { $python = 'py -3' }
    Set-Shim 'pdfid' "$python `"$(Join-Path $lib 'pdfid.py')`""
}

function Install-Steghide {
    # The upstream build is a folder of steghide.exe and its DLLs; it goes on
    # PATH as it is rather than behind a .cmd, which would mangle passwords.
    $zip = Join-Path $WorkDir 'steghide.zip'
    Get-Download "https://downloads.sourceforge.net/project/steghide/steghide/$SteghideVersion/steghide-$SteghideVersion-win32.zip" $zip $SteghideSha256
    Expand-Archive -LiteralPath $zip -DestinationPath $Prefix -Force
}

function Install-Jsteg {
    Get-Download "https://github.com/lukechampine/jsteg/releases/download/v$JstegVersion/jsteg-windows-amd64.exe" (Join-Path $BinDir 'jsteg.exe') $JstegSha256
}

function Install-Openstego {
    if (-not (Test-Tool 'java')) { throw 'java is not installed' }
    $zip = Join-Path $WorkDir 'openstego.zip'
    Get-Download "https://github.com/syvaidya/openstego/releases/download/openstego-$OpenStegoVersion/openstego-$OpenStegoVersion.zip" $zip $OpenStegoSha256
    Expand-Archive -LiteralPath $zip -DestinationPath $WorkDir -Force
    $lib = Join-Path $Prefix 'lib'
    New-Item -ItemType Directory -Force -Path $lib | Out-Null
    Copy-Item -LiteralPath (Join-Path $WorkDir "openstego-$OpenStegoVersion\lib\openstego.jar") -Destination $lib -Force
    Set-Shim 'openstego' "java -Xmx1024m -jar `"$(Join-Path $lib 'openstego.jar')`""
}

function Install-Zsteg {
    if (-not (Test-Tool 'gem')) { Install-ScoopPackage 'ruby' }
    Invoke-Tool 'gem' @('install', '--no-document', 'zsteg')
}

# ---------------------------------------------------------------------------

if ($env:OS -ne 'Windows_NT') {
    [Console]::Error.WriteLine('this script is for Windows; use install-tools.sh on Linux and macOS')
    exit 2
}
if ($env:PROCESSOR_ARCHITECTURE -ne 'AMD64' -and -not $DryRun) {
    Write-Warning "untested on $env:PROCESSOR_ARCHITECTURE; the downloaded tools are x86-64 builds"
}

Write-Info "Windows, package manager: Scoop, prefix: $Prefix"

if (-not $DryRun) {
    New-Item -ItemType Directory -Force -Path $BinDir | Out-Null
    Update-SessionPath
}

foreach ($tool in $ScoopPackages.Keys) {
    if (Test-Tool $tool) { continue }
    try { Install-ScoopPackage $ScoopPackages[$tool] }
    catch { Write-Warning "could not install $($ScoopPackages[$tool]): $_" }
}

if (-not $DryRun) {
    # ImageMagick 7 ships only magick.exe unless its legacy tools are selected.
    if (-not (Test-Tool 'identify') -and (Test-Tool 'magick')) { Set-Shim 'identify' 'magick identify' }
    $WorkDir = Join-Path ([IO.Path]::GetTempPath()) "stegray-tools-$PID"
    New-Item -ItemType Directory -Force -Path $WorkDir | Out-Null
}
try {
    foreach ($tool in $Extras) {
        if (Test-Tool $tool) { continue }
        if ($DryRun) {
            Write-Info "would install $tool into $Prefix"
            continue
        }
        Write-Info "installing $tool into $Prefix"
        try { & "Install-$tool" }
        catch { Write-Warning "could not install ${tool}: $_" }
    }
}
finally {
    if ($WorkDir) { Remove-Item -Recurse -Force -LiteralPath $WorkDir -ErrorAction SilentlyContinue }
}

if ($DryRun) { exit 0 }

Add-UserPath $BinDir
Add-UserPath (Join-Path $Prefix 'steghide')
Update-SessionPath

$missing = @(@($ScoopPackages.Keys) + $Extras | Where-Object { -not (Test-Tool $_) })
Write-Warning "not available natively on Windows: $($Unavailable -join ', ') (use WSL or the container image for these)"
if ($missing.Count -gt 0) {
    Write-Warning "still missing: $($missing -join ', ')"
    exit 1
}
Write-Info 'done; open a new terminal and check with: stegray --list-tools'
