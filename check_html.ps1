$html = Get-Content 'dev/app/resources/ui/index.html' -Raw
$divOpen = ([regex]::Matches($html, '<div\b')).Count
$divClose = ([regex]::Matches($html, '</div>')).Count
Write-Output ('div open: ' + $divOpen + ' close: ' + $divClose)
$styleOpen = ([regex]::Matches($html, '<style\b')).Count
$styleClose = ([regex]::Matches($html, '</style>')).Count
Write-Output ('style open: ' + $styleOpen + ' close: ' + $styleClose)
$veil = (Select-String -Path 'dev/app/resources/ui/index.html' -Pattern 'yunji-login-veil' -AllMatches).Matches.Count
$box = (Select-String -Path 'dev/app/resources/ui/index.html' -Pattern 'user-info-box' -AllMatches).Matches.Count
$ltx = (Select-String -Path 'dev/app/resources/ui/index.html' -Pattern 'LTX-2 STUDIO' -AllMatches).Matches.Count
Write-Output ('yunji-login-veil: ' + $veil)
Write-Output ('user-info-box: ' + $box)
Write-Output ('LTX-2 STUDIO remaining: ' + $ltx)
