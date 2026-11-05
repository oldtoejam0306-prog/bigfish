$ErrorActionPreference = 'Stop'
[Console]::InputEncoding = [Text.Encoding]::UTF8
$task = [Console]::In.ReadToEnd() | ConvertFrom-Json
$voice = New-Object -ComObject SAPI.SpVoice
$chosen = $null
foreach ($token in $voice.GetVoices()) { if ($token.GetDescription() -eq 'Microsoft Huihui Desktop - Chinese (Simplified)') { $chosen = $token; break } }
if ($null -eq $chosen) { exit 7 }
$stream = New-Object -ComObject SAPI.SpFileStream
try { $voice.Voice = $chosen; $stream.Open($task.output, 3, $false); $voice.AudioOutputStream = $stream; [void]$voice.Speak($task.text, 0) }
finally { $stream.Close() }
