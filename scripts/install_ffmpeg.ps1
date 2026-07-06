$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$ffmpegExe = Join-Path $root 'ffmpeg\bin\ffmpeg.exe'
if (Test-Path $ffmpegExe) {
    Write-Host '[OK] FFmpeg already installed'
    exit 0
}
Write-Host '[..] Downloading portable FFmpeg...'
$zip = Join-Path $root 'ffmpeg.zip'
$url = 'https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip'
Invoke-WebRequest -Uri $url -OutFile $zip -UseBasicParsing
$tmp = Join-Path $root 'ffmpeg_tmp'
Expand-Archive -Path $zip -DestinationPath $tmp -Force
$dir = Get-ChildItem $tmp -Directory | Select-Object -First 1
$dest = Join-Path $root 'ffmpeg\bin'
New-Item -ItemType Directory -Force -Path $dest | Out-Null
Copy-Item -Path (Join-Path $dir.FullName 'bin\*') -Destination $dest -Recurse -Force
Remove-Item $zip -Force
Remove-Item $tmp -Recurse -Force
Write-Host '[OK] FFmpeg installed to ffmpeg\bin\'
