# Dot-source this script to activate Python and use E-drive development storage.
# Usage from repository root: . .\scripts\Activate-FactoryMemory.ps1
$factoryRepo = Split-Path -Parent $PSScriptRoot
$factoryStorage = [Environment]::GetEnvironmentVariable('FACTORY_MEMORY_STORAGE', 'User')
if (-not $factoryStorage) {
    $factoryStorage = Join-Path (Split-Path -Parent $factoryRepo) '.local-data'
}
$factoryPaths = @{
    FACTORY_MEMORY_STORAGE = $factoryStorage
    PIP_CACHE_DIR = Join-Path $factoryStorage 'cache\pip'
    PIP_DEST = Join-Path $factoryStorage 'downloads'
    XDG_CACHE_HOME = Join-Path $factoryStorage 'cache'
    HF_HOME = Join-Path $factoryStorage 'cache\huggingface'
    HF_HUB_CACHE = Join-Path $factoryStorage 'cache\huggingface\hub'
    HF_DATASETS_CACHE = Join-Path $factoryStorage 'cache\huggingface\datasets'
    HF_ASSETS_CACHE = Join-Path $factoryStorage 'cache\huggingface\assets'
    HF_XET_CACHE = Join-Path $factoryStorage 'cache\huggingface\xet'
    TORCH_HOME = Join-Path $factoryStorage 'cache\torch'
    NUMBA_CACHE_DIR = Join-Path $factoryStorage 'cache\numba'
    UV_CACHE_DIR = Join-Path $factoryStorage 'cache\uv'
    SENTENCE_TRANSFORMERS_HOME = Join-Path $factoryStorage 'cache\sentence-transformers'
    TEMP = Join-Path $factoryStorage 'tmp'
    TMP = Join-Path $factoryStorage 'tmp'
}
foreach ($factoryKey in $factoryPaths.Keys) {
    New-Item -ItemType Directory -Force -Path $factoryPaths[$factoryKey] | Out-Null
    [Environment]::SetEnvironmentVariable($factoryKey, $factoryPaths[$factoryKey], 'Process')
}
. (Join-Path $factoryRepo '.venv\Scripts\Activate.ps1')
Write-Host "Factory Memory storage: $factoryStorage"
