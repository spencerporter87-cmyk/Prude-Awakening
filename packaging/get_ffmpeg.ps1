# Downloads a current FFmpeg build (shared, GPL, includes NVENC + ffplay) into vendor/ffmpeg/bin.
# Uses only PowerShell (no GitHub CLI needed), so it works on a normal PC as well as in CI.
$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"
$headers = @{ "User-Agent" = "prude-awakening-build" }
if ($env:GH_TOKEN) { $headers["Authorization"] = "Bearer $env:GH_TOKEN" }
$rel = Invoke-RestMethod -Uri "https://api.github.com/repos/BtbN/FFmpeg-Builds/releases/latest" -Headers $headers
$pick = $rel.assets | Where-Object { $_.name -match '^ffmpeg-n(\d+)\.(\d+).*-win64-gpl-shared-.*\.zip$' } |
    Sort-Object { [int]($_.name -replace '^ffmpeg-n(\d+)\.(\d+).*', '$1') * 100 + [int]($_.name -replace '^ffmpeg-n(\d+)\.(\d+).*', '$2') } |
    Select-Object -Last 1
if (-not $pick) { throw "No FFmpeg build found" }
Write-Host "Using $($pick.name)"
New-Item -ItemType Directory -Force -Path vendor | Out-Null
Invoke-WebRequest -Uri $pick.browser_download_url -OutFile vendor/ffmpeg.zip
Expand-Archive vendor/ffmpeg.zip -DestinationPath vendor/ffmpeg-x -Force
$bin = Get-ChildItem vendor/ffmpeg-x -Recurse -Directory -Filter bin | Select-Object -First 1
New-Item -ItemType Directory -Force -Path vendor/ffmpeg/bin | Out-Null
Copy-Item "$($bin.FullName)\*" vendor/ffmpeg/bin -Recurse -Force
Remove-Item vendor/ffmpeg.zip, vendor/ffmpeg-x -Recurse -Force
Get-ChildItem vendor/ffmpeg/bin | Select-Object Name, Length
