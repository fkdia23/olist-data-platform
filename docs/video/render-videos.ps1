<#
Rend les trois démonstrations vidéo Olist, à partir des captures et des sous-titres.

Prérequis : FFmpeg. Exécution depuis la racine du dépôt :
  powershell -ExecutionPolicy Bypass -File docs/video/render-videos.ps1
#>

[CmdletBinding()]
param(
    [switch]$KeepIntermediate,
    [ValidateSet('nominal', 'qualite', 'contrat')]
    [string]$Only
)

$ErrorActionPreference = 'Stop'
$projectRoot = Resolve-Path (Join-Path $PSScriptRoot '../..')
$ffmpeg = 'C:\Users\fkngu\AppData\Local\Microsoft\WinGet\Packages\Gyan.FFmpeg.Shared_Microsoft.Winget.Source_8wekyb3d8bbwe\ffmpeg-9.0.2-full_build-shared\bin\ffmpeg.exe'

if (-not (Test-Path -LiteralPath $ffmpeg)) {
    $ffmpeg = (Get-Command ffmpeg -ErrorAction SilentlyContinue).Source
}
if (-not $ffmpeg) {
    throw 'FFmpeg est introuvable. Installez-le puis relancez ce script.'
}

$outputDirectory = Join-Path $PSScriptRoot 'output'
New-Item -ItemType Directory -Force -Path $outputDirectory | Out-Null

function Convert-ToFfmpegPath([string]$Path) {
    return ($Path -replace '\\', '/') -replace '^([A-Za-z]):', '$1\\:'
}

function New-Video {
    param(
        [string]$Name,
        [string]$Title,
        [string]$Subtitle,
        [string]$Srt,
        [array]$Slides
    )

    # Chemin relatif : cela évite l’échappement particulier du « C: » dans
    # la syntaxe de filtres FFmpeg.
    $srtPath = "docs/video/$Srt"
    $outputPath = Join-Path $outputDirectory "$Name.mp4"
    $arguments = @(
        '-hide_banner', '-loglevel', 'error', '-nostats', '-y',
        '-f', 'lavfi', '-t', '20', '-i', 'color=c=0x071923:s=1920x1080:r=12'
    )

    foreach ($slide in $Slides) {
        $arguments += @('-loop', '1', '-t', [string]$slide.Seconds, '-i', (Join-Path $projectRoot $slide.Image))
    }

    $arguments += @('-f', 'lavfi', '-t', '300', '-i', 'anullsrc=r=48000:cl=stereo')

    $filters = @(
        "[0:v]drawtext=fontfile='C\:/Windows/Fonts/arialbd.ttf':text='$Title':fontcolor=white:fontsize=70:x=(w-text_w)/2:y=400,drawtext=fontfile='C\:/Windows/Fonts/arial.ttf':text='$Subtitle':fontcolor=0x9cc9d9:fontsize=36:x=(w-text_w)/2:y=505,format=yuv420p[v0]"
    )
    for ($index = 0; $index -lt $Slides.Count; $index++) {
        $inputIndex = $index + 1
        $filters += "[${inputIndex}:v]scale=1920:1080:force_original_aspect_ratio=decrease,pad=1920:1080:(ow-iw)/2:(oh-ih)/2:color=0x071923,format=yuv420p[v$($index + 1)]"
    }
    $labels = 0..$Slides.Count | ForEach-Object { "[v$_]" }
    $filters += "$($labels -join '')concat=n=$($Slides.Count + 1):v=1:a=0[base]"
    $filters += "[base]subtitles=filename='$srtPath':fontsdir='C\:/Windows/Fonts':charenc=UTF-8:force_style='Fontname=Arial,Fontsize=14,PrimaryColour=&H00FFFFFF,BackColour=&H99000000,BorderStyle=4,Outline=1,Shadow=0,Alignment=2,MarginV=18'[video]"

    $filterComplex = $filters -join ';'
    $arguments += @(
        '-filter_complex', $filterComplex,
        '-map', '[video]', '-map', "$($Slides.Count + 1):a:0",
        '-c:v', 'libx264', '-preset', 'medium', '-crf', '22', '-r', '12', '-pix_fmt', 'yuv420p',
        '-c:a', 'aac', '-b:a', '128k', '-movflags', '+faststart',
        $outputPath
    )

    Write-Host "Rendu de $Name..."
    & $ffmpeg @arguments
    if ($LASTEXITCODE -ne 0) { throw "Le rendu de $Name a échoué." }
}

if (-not $Only -or $Only -eq 'nominal') {
    New-Video -Name '01-nominal' -Title 'Olist Data Platform' -Subtitle 'Scenario 1 - Une journee type' -Srt '01-nominal.srt' -Slides @(
        @{ Image = 'docs/images/01-streamlit-nominal.png'; Seconds = 145 },
        @{ Image = 'docs/images/02-mailpit-recap-nominal.png'; Seconds = 55 },
        @{ Image = 'docs/images/03-marquez-lineage-duckdb.png'; Seconds = 55 },
        @{ Image = 'docs/images/03b-marquez-lineage-job.png'; Seconds = 25 }
    )
}

if (-not $Only -or $Only -eq 'qualite') {
    New-Video -Name '02-qualite-degradee' -Title 'Olist Data Platform' -Subtitle 'Scenario 3 - Le piege silencieux' -Srt '02-qualite-degradee.srt' -Slides @(
        @{ Image = 'docs/images/04-streamlit-qualite.png'; Seconds = 280 }
    )
}

if (-not $Only -or $Only -eq 'contrat') {
    New-Video -Name '03-contrat-casse' -Title 'Olist Data Platform' -Subtitle 'Scenario 2 - L incident franc' -Srt '03-contrat-casse.srt' -Slides @(
        @{ Image = 'docs/images/05-streamlit-contrat-casse.png'; Seconds = 170 },
        @{ Image = 'docs/images/06-mailpit-boite.png'; Seconds = 55 },
        @{ Image = 'docs/images/07-mailpit-alerte-grafana.png'; Seconds = 55 }
    )
}

Write-Host "Vidéos créées dans : $outputDirectory"
