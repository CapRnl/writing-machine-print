# 安装"写字机打印"的运行环境：建 .venv、装依赖（清华镜像）、在开始菜单建快捷方式。由 安装.bat 调用。
# 中文提示放在这里而不放在 .bat 里：批处理文件里有中文时，命令行读文件会串行（09-28 实测"&& was unexpected"）。
param([string]$LinkPath = '')
$app = Split-Path -Parent $PSScriptRoot
Set-Location $app
Write-Host '正在安装"写字机打印"的运行环境（第一次大约 1-3 分钟，需要联网）……'
Write-Host ''

# 找 Python 3：先用 Python 官方安装包带的 py，再找 python（系统自带的"应用商店占位"python 会被识别为没装）
$py = $null
foreach ($cand in @('py -3', 'python')) {
    $exe, $rest = $cand.Split(' ', 2)
    try {
        if ($rest) { $v = & $exe $rest --version 2>&1 } else { $v = & $exe --version 2>&1 }
        if ($LASTEXITCODE -eq 0 -and "$v" -match 'Python 3') { $py = $cand; break }
    } catch { }
}
if (-not $py) {
    Write-Host '没找到 Python。请先到 https://www.python.org/downloads/ 下载安装 Python 3.12，'
    Write-Host '安装时勾选"Add python.exe to PATH"，装好后再双击 安装.bat。'
    exit 1
}

if (-not (Test-Path '.venv\Scripts\python.exe')) {
    Write-Host "用 $py 建运行环境（.venv）……"
    $exe, $rest = $py.Split(' ', 2)
    if ($rest) { & $exe $rest -m venv .venv } else { & $exe -m venv .venv }
    if ($LASTEXITCODE -ne 0) { Write-Host '建运行环境失败，上面有出错的原因。'; exit 1 }
}

Write-Host '安装依赖……'
& '.venv\Scripts\python.exe' -m pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
if ($LASTEXITCODE -ne 0) { Write-Host '装依赖失败，多半是网络问题，上面有出错的原因。'; exit 1 }

& (Join-Path $PSScriptRoot 'make_shortcut.ps1') -LinkPath $LinkPath
if ($LASTEXITCODE -ne 0) { exit 1 }
Write-Host ''
Write-Host '装好了：开始菜单里有"写字机打印"。'
Write-Host '还差一步：准备字库，见 fonts 文件夹里的"字库说明.txt"。'
