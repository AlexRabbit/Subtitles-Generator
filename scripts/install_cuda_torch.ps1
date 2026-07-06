$ErrorActionPreference = 'Continue'
$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$python = Join-Path $root 'venv\Scripts\python.exe'
$pip = Join-Path $root 'venv\Scripts\pip.exe'
if (-not (Test-Path $python)) { exit 0 }

$nvidia = Get-Command nvidia-smi -ErrorAction SilentlyContinue
if (-not $nvidia) {
    Write-Host '[INFO] No NVIDIA GPU detected - using CPU PyTorch'
    exit 0
}

# Skip if CUDA already works
$already = & $python -c "import torch; print(torch.cuda.is_available())" 2>&1
if ($already -eq 'True') {
    $name = & $python -c "import torch; print(torch.cuda.get_device_name(0))" 2>&1
    Write-Host "[OK] CUDA already active - $name"
    exit 0
}

Write-Host '[..] NVIDIA GPU detected - installing CUDA PyTorch...'
$indexes = @('cu128', 'cu126', 'cu124', 'cu121')
foreach ($cu in $indexes) {
    Write-Host "     Trying $cu..."
    & $pip uninstall -y torch torchaudio 2>&1 | Out-Null
    & $pip install torch torchaudio --index-url "https://download.pytorch.org/whl/$cu" 2>&1 | Out-Null
    $check = & $python -c "import torch; print(torch.cuda.is_available())" 2>&1
    if ($check -eq 'True') {
        $name = & $python -c "import torch; print(torch.cuda.get_device_name(0))" 2>&1
        Write-Host "[OK] CUDA PyTorch ready ($cu) - $name"
        exit 0
    }
}
Write-Host '[WARN] CUDA PyTorch install failed - CPU mode will be used'
& $pip install torch torchaudio 2>&1 | Out-Null
