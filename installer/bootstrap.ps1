# OnDaGo Editor - one-click installer (Windows 10/11, NVIDIA GPU recommended)
# Installs everything INSIDE this folder: uv, Python 3.11, venv, PyTorch (CUDA), FFmpeg, Ollama, all models.
# Safe to re-run: finished steps are skipped. No admin rights needed.
param(
    [switch]$Lite,                  # skip the 16 GB FLUX image model
    [string]$Llm = "qwen3.5:9b"     # editor LLM (Ollama tag)
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

# Disable console QuickEdit: a stray click in the window otherwise PAUSES the whole install
try {
    Add-Type -Namespace AE -Name Con -MemberDefinition @'
[DllImport("kernel32.dll")] public static extern IntPtr GetStdHandle(int h);
[DllImport("kernel32.dll")] public static extern bool GetConsoleMode(IntPtr h, out uint m);
[DllImport("kernel32.dll")] public static extern bool SetConsoleMode(IntPtr h, uint m);
'@
    $h = [AE.Con]::GetStdHandle(-10); $m = 0
    if ([AE.Con]::GetConsoleMode($h, [ref]$m)) { [void][AE.Con]::SetConsoleMode($h, ($m -band (-bnot 0x40)) -bor 0x80) }
} catch { }

$Root  = Split-Path -Parent $PSScriptRoot
$Tools = Join-Path $Root "tools"
$Venv  = Join-Path $Root ".venv"
$Py    = Join-Path $Venv "Scripts\python.exe"
New-Item -ItemType Directory -Force -Path $Tools | Out-Null
# Work from the install folder and hand tools RELATIVE paths: some Windows setups split
# arguments at spaces (e.g. C:\Users\Designer 1\...), which broke "uv pip install -r".
Set-Location -LiteralPath $Root
$RelPy = ".venv\Scripts\python.exe"
Start-Transcript -Path (Join-Path $Root "install.log") -Append | Out-Null

function Say($msg)  { Write-Host ""; Write-Host "==> $msg" -ForegroundColor Cyan }
function Ok($msg)   { Write-Host "    [ok] $msg" -ForegroundColor Green }
function Warn($msg) { Write-Host "    [!] $msg" -ForegroundColor Yellow }

function Get-File([string]$Url, [string]$Dest) {
    if ((Test-Path $Dest) -and ((Get-Item $Dest).Length -gt 0)) { Ok "cached $(Split-Path $Dest -Leaf)"; return }
    $part = "$Dest.part"
    for ($i = 1; $i -le 3; $i++) {
        & curl.exe -L --fail --retry 5 --retry-delay 3 -C - -o $part $Url
        if ($LASTEXITCODE -eq 0) { Move-Item -Force $part $Dest; return }
        Warn "download attempt $i failed ($Url)"
        Start-Sleep -Seconds 3
    }
    throw "Download failed: $Url"
}

function Expand-Zip([string]$Zip, [string]$Dest) {
    New-Item -ItemType Directory -Force -Path $Dest | Out-Null
    & tar.exe -xf $Zip -C $Dest
    if ($LASTEXITCODE -ne 0) { Expand-Archive -Force -Path $Zip -DestinationPath $Dest }
}

Write-Host "OnDaGo Editor installer" -ForegroundColor Magenta
Write-Host "Install folder: $Root"
if ($Root -match '\s') { Warn "Install path contains a space. If anything fails, move this folder to e.g. C:\OnDaGo and re-run." }
if ($Root.Length -gt 60) { Warn "Install path is long. If you see path errors, move this folder to e.g. C:\OnDaGo and re-run." }

# ------------------------------------------------------------------ checks
Say "Checking system"
if (-not (Get-Command curl.exe -ErrorAction SilentlyContinue)) { throw "curl.exe not found (needs Windows 10 1803 or newer)." }
$drive = (Get-Item $Root).PSDrive
$freeGB = [math]::Round($drive.Free / 1GB, 1)
$needGB = 45; if ($Lite) { $needGB = 25 }
if ($freeGB -lt $needGB) { Warn "Only $freeGB GB free on $($drive.Name):. About $needGB GB is needed." } else { Ok "$freeGB GB free" }

$cuTag = "cpu"
$smi = Get-Command nvidia-smi -ErrorAction SilentlyContinue
if ($smi) {
    $gpu = (& nvidia-smi --query-gpu=name,memory.total,driver_version --format=csv,noheader) -join "; "
    Ok "GPU: $gpu"
    $head = (& nvidia-smi) -join "`n"
    $cudaMajor = 0; $cudaMinor = 0
    if ($head -match "CUDA Version:\s*(\d+)\.(\d+)") { $cudaMajor = [int]$Matches[1]; $cudaMinor = [int]$Matches[2] }
    $cv = $cudaMajor * 10 + $cudaMinor
    $cc = 0.0
    try { $cc = [double]((& nvidia-smi --query-gpu=compute_cap --format=csv,noheader | Select-Object -First 1).Trim()) } catch { }
    if ($cc -gt 0) { Ok "GPU compute capability: $cc" }
    if ($cc -gt 0 -and $cc -lt 7.5) {
        # Pascal / Maxwell (GTX 10xx/9xx): newest CUDA 12.8 PyTorch builds dropped these GPUs, cu126 still has them
        $cuTag = "cu126"
        if (-not $Lite) {
            $Lite = $true
            Warn "GTX-class GPU detected: skipping the AI image model (too slow on this card). Thumbnails use blurred/gradient backgrounds."
        }
    }
    elseif ($cv -ge 128) { $cuTag = "cu128" }
    elseif ($cv -ge 126) { $cuTag = "cu126" }
    else {
        $cuTag = "cu126"
        Warn "Your NVIDIA driver supports CUDA $cudaMajor.$cudaMinor. Please update the NVIDIA driver (560+) for GPU acceleration."
    }
    Ok "PyTorch build: $cuTag"
} else {
    Warn "No NVIDIA GPU detected - the app will run on CPU (much slower)."
}

# ------------------------------------------------------------------ uv + python
Say "Python runtime (uv)"
$UvDir = Join-Path $Tools "uv"
$Uv = Join-Path $UvDir "uv.exe"
if (-not (Test-Path $Uv)) {
    $zip = Join-Path $Tools "uv.zip"
    Get-File "https://github.com/astral-sh/uv/releases/latest/download/uv-x86_64-pc-windows-msvc.zip" $zip
    Expand-Zip $zip $UvDir
    if (-not (Test-Path $Uv)) {
        $found = Get-ChildItem -Recurse -Path $UvDir -Filter uv.exe | Select-Object -First 1
        Copy-Item $found.FullName $Uv
    }
    Remove-Item $zip -Force
}
Ok "uv ready"
$env:UV_PYTHON_INSTALL_DIR = Join-Path $Tools "python"
$env:UV_CACHE_DIR = Join-Path $Tools "uv-cache"
$env:UV_LINK_MODE = "copy"
$env:UV_HTTP_TIMEOUT = "300"
if (-not (Test-Path $Py)) {
    & $Uv venv .venv --python 3.11 --seed
    if ($LASTEXITCODE -ne 0) { throw "Could not create Python environment." }
}
Ok "Python environment: $Venv"

# ------------------------------------------------------------------ packages
Say "PyTorch ($cuTag) - large download, please wait"
$torchIdx = "https://download.pytorch.org/whl/$cuTag"
$ok = $false
for ($i = 1; $i -le 3; $i++) {
    & $Uv pip install --python $RelPy torch torchvision --index-url $torchIdx --reinstall-package torch --reinstall-package torchvision
    if ($LASTEXITCODE -eq 0) { $ok = $true; break }
    Warn "PyTorch download interrupted (attempt $i of 3), retrying..."
    Start-Sleep -Seconds 5
}
if (-not $ok) { throw "PyTorch install failed (network). Re-run install.bat to continue." }
$tv = (& $Py -c "import torch,torchvision;print(torch.__version__);print(torchvision.__version__)")
$constraints = "tools\constraints.txt"
Set-Content -Path $constraints -Value @("torch==$($tv[0])", "torchvision==$($tv[1])") -Encoding ascii
Ok "torch $($tv[0])"

Say "App libraries"
& $Uv pip install --python $RelPy -r requirements.txt -c $constraints --extra-index-url $torchIdx --index-strategy unsafe-best-match
if ($LASTEXITCODE -ne 0) { throw "Library install failed." }
Ok "libraries installed"

# ------------------------------------------------------------------ ffmpeg
Say "FFmpeg (with NVENC + libass)"
$FfBin = Join-Path $Tools "ffmpeg\bin"
if (-not (Test-Path (Join-Path $FfBin "ffmpeg.exe"))) {
    $zip = Join-Path $Tools "ffmpeg.zip"
    try {
        Get-File "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-gpl.zip" $zip
    } catch {
        Warn "BtbN mirror failed, trying gyan.dev"
        Get-File "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip" $zip
    }
    $tmp = Join-Path $Tools "ffmpeg-tmp"
    Expand-Zip $zip $tmp
    $exe = Get-ChildItem -Recurse -Path $tmp -Filter ffmpeg.exe | Select-Object -First 1
    New-Item -ItemType Directory -Force -Path $FfBin | Out-Null
    Copy-Item (Join-Path $exe.DirectoryName "*") $FfBin -Recurse -Force
    Remove-Item $tmp -Recurse -Force; Remove-Item $zip -Force
}
Ok "ffmpeg ready"

# ------------------------------------------------------------------ ollama
Say "Ollama (local LLM runtime, portable)"
$OlDir = Join-Path $Tools "ollama"
if (-not (Test-Path (Join-Path $OlDir "ollama.exe"))) {
    $zip = Join-Path $Tools "ollama.zip"
    Get-File "https://github.com/ollama/ollama/releases/latest/download/ollama-windows-amd64.zip" $zip
    Expand-Zip $zip $OlDir
    Remove-Item $zip -Force
}
Ok "ollama ready"

# ------------------------------------------------------------------ models
Say "Downloading AI models (Whisper, Qwen 3.5, SigLIP, BiRefNet$(if (-not $Lite) {', FLUX.2 klein'}))"
$env:HF_HUB_DISABLE_SYMLINKS_WARNING = "1"
$args2 = @("installer\setup_models.py", "--llm", $Llm)
if ($Lite) { $args2 += "--lite" }
& $Py @args2
if ($LASTEXITCODE -ne 0) { throw "Model setup reported a critical failure (see above)." }

# ------------------------------------------------------------------ shortcut
Say "Creating desktop shortcut"
try {
    $ws = New-Object -ComObject WScript.Shell
    $desk = [Environment]::GetFolderPath("Desktop")
    Remove-Item (Join-Path $desk "AutoEdit Studio.lnk") -ErrorAction SilentlyContinue   # old name
    $lnk = $ws.CreateShortcut((Join-Path $desk "OnDaGo Editor.lnk"))
    $lnk.TargetPath = Join-Path $Venv "Scripts\pythonw.exe"
    $lnk.Arguments = "-m app.main"
    $lnk.WorkingDirectory = $Root
    $ico = Join-Path $Root "app\gui\icon.ico"
    if (Test-Path $ico) { $lnk.IconLocation = $ico }
    $lnk.Save()
    Ok "Desktop shortcut created"
} catch { Warn "Could not create shortcut: $_" }

Stop-Transcript | Out-Null
Write-Host ""
Write-Host "Install complete! Start the app with the desktop shortcut or run.bat" -ForegroundColor Green
