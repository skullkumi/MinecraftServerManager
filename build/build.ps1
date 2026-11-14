# 配布物を作成する (開発者向け)
# 使い方: PowerShellで  .\build\build.ps1
#
# 作成されるもの (dist\):
#   MinecraftServerManager-v<版>.zip          ポータブル版 (単体EXE + ドキュメント)
#   MinecraftServerManager-v<版>-folder.zip   フォルダー版 (起動が速い)
#   MinecraftServerManager-Setup-v<版>.exe    インストーラー (Inno Setup がある場合のみ)
#   *.sha256 / latest.json                     検証用ハッシュ・更新情報 (アプリの自動更新で使用)
#
# コード署名 (任意): 次のどちらかを設定すると signtool で署名する
#   $env:MSM_SIGN_PFX = 'C:\path\cert.pfx'; $env:MSM_SIGN_PASSWORD = '...'
#   $env:MSM_SIGN_THUMBPRINT = '証明書の拇印' (証明書ストアの証明書を使う)
#   $env:MSM_SIGN_TIMESTAMP = 'http://timestamp.digicert.com' (省略時はこのURL。'none' でタイムスタンプなし)
# 更新情報JSONのダウンロードURLの先頭: $env:MSM_RELEASE_BASE_URL = 'https://example.com/releases/v1.0.0/'
param([switch]$SkipTests)
$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent
Set-Location $root
$py = Join-Path $root '.venv\Scripts\python.exe'
if (-not (Test-Path $py)) {
    python -m venv .venv
    & $py -m pip install -r requirements-dev.txt
}
$version = (& $py -c "import msm; print(msm.APP_VERSION)").Trim()
Write-Host "バージョン: $version"

if (-not $SkipTests) {
    & $py -m pytest -q tests/test_unit.py tests/test_automation_unit.py tests/test_more_unit.py tests/test_upnp_unit.py tests/test_snapshot_unit.py tests/test_ui_unit.py
    if ($LASTEXITCODE -ne 0) { throw '単体テストに失敗しました' }
}

function Find-SignTool {
    $c = Get-ChildItem "${env:ProgramFiles(x86)}\Windows Kits\10\bin\*\x64\signtool.exe" -ErrorAction SilentlyContinue |
        Sort-Object FullName -Descending | Select-Object -First 1
    if ($c) { return $c.FullName }
    return $null
}

function Sign-File([string]$path) {
    if (-not $env:MSM_SIGN_PFX -and -not $env:MSM_SIGN_THUMBPRINT) { return }
    $tool = Find-SignTool
    if (-not $tool) { throw 'signtool.exe が見つかりません (Windows SDK をインストールしてください)' }
    $a = @('sign', '/fd', 'SHA256')
    if ($env:MSM_SIGN_PFX) { $a += @('/f', $env:MSM_SIGN_PFX); if ($env:MSM_SIGN_PASSWORD) { $a += @('/p', $env:MSM_SIGN_PASSWORD) } }
    else { $a += @('/sha1', $env:MSM_SIGN_THUMBPRINT) }
    $ts = if ($env:MSM_SIGN_TIMESTAMP) { $env:MSM_SIGN_TIMESTAMP } else { 'http://timestamp.digicert.com' }
    if ($ts -ne 'none') { $a += @('/tr', $ts, '/td', 'SHA256') }
    $a += $path
    & $tool @a
    if ($LASTEXITCODE -ne 0) { throw "署名に失敗しました: $path" }
    Write-Host "署名しました: $path"
}

function Write-Sha256([string]$path) {
    $h = (Get-FileHash $path -Algorithm SHA256).Hash.ToLower()
    "$h  $(Split-Path $path -Leaf)" | Set-Content "$path.sha256" -Encoding ASCII
    return $h
}

# ---- PyInstaller (単体EXE / フォルダー) ----
Remove-Item dist -Recurse -Force -ErrorAction SilentlyContinue
$env:MSM_BUILD_MODE = 'onefile'
& $py -m PyInstaller --noconfirm --clean --distpath dist --workpath build\work build\msm.spec
if ($LASTEXITCODE -ne 0) { throw 'PyInstaller (onefile) に失敗しました' }
Move-Item dist\MinecraftServerManager.exe dist\MinecraftServerManager-portable.exe
$env:MSM_BUILD_MODE = 'onedir'
& $py -m PyInstaller --noconfirm --distpath dist --workpath build\work-onedir build\msm.spec
if ($LASTEXITCODE -ne 0) { throw 'PyInstaller (onedir) に失敗しました' }
Remove-Item Env:\MSM_BUILD_MODE
Sign-File dist\MinecraftServerManager-portable.exe
Sign-File dist\MinecraftServerManager\MinecraftServerManager.exe

$docs = @('docs\README.md', 'docs\はじめにお読みください.txt', 'docs\LICENSE.txt', 'docs\THIRD_PARTY_LICENSES.txt',
          'docs\CHANGELOG.md', 'docs\FEATURES.md')

# ---- ポータブル版ZIP ----
$name = "MinecraftServerManager-v$version"
$stage = Join-Path $root "dist\stage-portable"
New-Item -ItemType Directory $stage | Out-Null
Copy-Item dist\MinecraftServerManager-portable.exe (Join-Path $stage 'MinecraftServerManager.exe')
foreach ($d in $docs) { Copy-Item $d $stage }
$zip1 = Join-Path $root "dist\$name.zip"
Compress-Archive -Path "$stage\*" -DestinationPath $zip1
$h1 = Write-Sha256 $zip1

# ---- フォルダー版ZIP ----
$stage2 = Join-Path $root "dist\stage-folder\MinecraftServerManager"
New-Item -ItemType Directory $stage2 -Force | Out-Null
Copy-Item dist\MinecraftServerManager\* $stage2 -Recurse
foreach ($d in $docs) { Copy-Item $d $stage2 }
$zip2 = Join-Path $root "dist\$name-folder.zip"
Compress-Archive -Path (Join-Path $root 'dist\stage-folder\MinecraftServerManager') -DestinationPath $zip2
$h2 = Write-Sha256 $zip2

# ---- インストーラー (Inno Setup) ----
$iscc = @("${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe", "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe",
          "$env:ProgramFiles\Inno Setup 6\ISCC.exe") | Where-Object { Test-Path $_ } | Select-Object -First 1
$h3 = ''
if ($iscc) {
    & $iscc "/DAppVersion=$version" "/DSourceDir=$root\dist\MinecraftServerManager" "/DOutputDir=$root\dist" build\installer.iss
    if ($LASTEXITCODE -ne 0) { throw 'インストーラーの作成に失敗しました' }
    $setup = Join-Path $root "dist\MinecraftServerManager-Setup-v$version.exe"
    Sign-File $setup
    $h3 = Write-Sha256 $setup
} else {
    Write-Host 'Inno Setup が見つからないため、インストーラーの作成をスキップしました (https://jrsoftware.org/isinfo.php)' -ForegroundColor Yellow
}

# ---- 更新情報 (latest.json) ----
$base = if ($env:MSM_RELEASE_BASE_URL) { $env:MSM_RELEASE_BASE_URL } else { '' }
$dl = [ordered]@{
    onefile = [ordered]@{ url = "$base$name.zip"; sha256 = $h1 }
    onedir  = [ordered]@{ url = "$base$name-folder.zip"; sha256 = $h2 }
}
if ($h3) { $dl.installer = [ordered]@{ url = "${base}MinecraftServerManager-Setup-v$version.exe"; sha256 = $h3 } }
$latest = [ordered]@{ version = $version; url = $base; notes = ''; downloads = $dl }
$latest | ConvertTo-Json -Depth 5 | Set-Content (Join-Path $root 'dist\latest.json') -Encoding UTF8

Remove-Item (Join-Path $root 'dist\stage-portable') -Recurse -Force
Remove-Item (Join-Path $root 'dist\stage-folder') -Recurse -Force
Write-Host "完成: dist\"
Get-ChildItem dist -File | ForEach-Object { Write-Host ("  {0}  ({1:N1} MB)" -f $_.Name, ($_.Length / 1MB)) }
