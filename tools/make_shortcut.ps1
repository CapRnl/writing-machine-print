# 在开始菜单建"写字机打印"快捷方式：用本目录 .venv 里的 pythonw 打开，不弹黑框。
# 用法：安装.bat 会自动运行它；也可以 powershell -ExecutionPolicy Bypass -File tools\make_shortcut.ps1
param([string]$LinkPath = '')
try {
    $app = Split-Path -Parent $PSScriptRoot
    if (-not $LinkPath) { $LinkPath = Join-Path ([Environment]::GetFolderPath('Programs')) '写字机打印.lnk' }
    $py = Join-Path $app '.venv\Scripts\pythonw.exe'
    if (-not (Test-Path $py)) { throw "没找到 $py，先运行 安装.bat 建好运行环境" }
    $ws = New-Object -ComObject WScript.Shell
    $s = $ws.CreateShortcut($LinkPath)
    $s.TargetPath = $py
    $s.Arguments = '"' + (Join-Path $app '写字机打印.pyw') + '"'
    $s.WorkingDirectory = $app
    $s.IconLocation = (Join-Path $app '写字机打印.ico') + ',0'
    $s.Description = '把会议记录、政治理论学习的 Word 原稿直接交给写字机书写'
    $s.Save()
    "已建好快捷方式：$LinkPath"
} catch { '出错：' + $_.Exception.Message; exit 1 }
