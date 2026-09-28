# 冒烟测试：隐藏窗口启动一次程序，检查界面脚本和后台接口都通了，结果写到 冒烟测试结果.txt 后自动退出。
try {
    $app = Split-Path -Parent $PSScriptRoot
    $res = Join-Path $app '冒烟测试结果.txt'
    if (Test-Path $res) { Remove-Item $res -Force }
    $env:HWPRINT_SMOKE = '1'
    $p = Start-Process -FilePath "$app\.venv\Scripts\pythonw.exe" -ArgumentList ('"' + "$app\写字机打印.pyw" + '"') -WorkingDirectory $app -PassThru
    $ok = $p.WaitForExit(60000)
    if (-not $ok) { Stop-Process -Id $p.Id -Force; '超时' }
    if (Test-Path $res) { '结果：' + (Get-Content $res -Raw -Encoding UTF8) } else { '没有结果文件' }
    if (Test-Path "$app\启动出错.txt") { '启动出错：' + (Get-Content "$app\启动出错.txt" -Raw -Encoding UTF8) }
} catch { '出错：' + $_.Exception.Message }
