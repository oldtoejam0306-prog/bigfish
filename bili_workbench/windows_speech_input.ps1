$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$stream = $null
$recognizer = $null
try {
    Add-Type -AssemblyName System.Speech
    $stream = [System.IO.MemoryStream]::new()
    [Console]::OpenStandardInput().CopyTo($stream)
    $stream.Position = 0
    $culture = [System.Globalization.CultureInfo]::GetCultureInfo('zh-CN')
    $recognizer = [System.Speech.Recognition.SpeechRecognitionEngine]::new($culture)
    $recognizer.LoadGrammar([System.Speech.Recognition.DictationGrammar]::new())
    $recognizer.SetInputToWaveStream($stream)
    $result = $recognizer.Recognize([TimeSpan]::FromSeconds(15))
    if ($null -eq $result) { @{ text = ''; recognized = $false } | ConvertTo-Json -Compress }
    else { @{ text = [string]$result.Text; recognized = $true } | ConvertTo-Json -Compress }
} catch {
    [Console]::Error.WriteLine('Local speech recognition unavailable.')
    exit 1
} finally {
    if ($null -ne $recognizer) { $recognizer.Dispose() }
    if ($null -ne $stream) { $stream.Dispose() }
}
