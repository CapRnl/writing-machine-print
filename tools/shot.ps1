param([string]$Url = 'http://127.0.0.1:8765/?autoload=1', [string]$Out, [int]$W = 1400, [int]$H = 900, [int]$Wait = 9000)
# 用 Edge 无界面模式给测试界面截图（不弹窗口、不抢前台）
$edge = 'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe'
$prof = Join-Path $env:TEMP 'hwprint-edge-shot'
if (-not $Out) { $Out = Join-Path $env:TEMP 'hwprint-shot.png' }
if (Test-Path $Out) { Remove-Item $Out -Force }
$args = @('--headless=new', '--disable-gpu', '--hide-scrollbars', "--user-data-dir=$prof", "--window-size=$W,$H",
          "--virtual-time-budget=$Wait", "--screenshot=$Out", $Url)
$p = Start-Process -FilePath $edge -ArgumentList $args -PassThru -WindowStyle Hidden
$p.WaitForExit(60000) | Out-Null
if (Test-Path $Out) { "saved $Out" } else { "no screenshot" }
