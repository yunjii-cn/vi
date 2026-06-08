$url = 'https://vi.yunjii.cn/sl/tutorial-v3.html'
$resp = Invoke-WebRequest -Uri $url -UseBasicParsing -TimeoutSec 15 -Headers @{'Cache-Control'='no-cache';'Pragma'='no-cache'}
$bytes = $resp.RawContentStream.ToArray()
$md5 = [System.BitConverter]::ToString(([System.Security.Cryptography.MD5]::Create()).ComputeHash($bytes)).Replace('-','').ToLower()
Write-Output ('MD5: ' + $md5)
$content = [System.Text.Encoding]::UTF8.GetString($bytes)
Write-Output '---KEY FRAGMENTS---'
$content -split '[\r\n]+' | Select-String -Pattern 'callback.{0,5}自己|queue|Queue|TcpListener|callback.{0,10}islogin|EXE.{0,5}直接开|EXE.{0,5}login\.php|/start|scene-b|/callback' | ForEach-Object { $_.Line } | Select-Object -First 40
Write-Output '---LENGTH---'
Write-Output $content.Length
