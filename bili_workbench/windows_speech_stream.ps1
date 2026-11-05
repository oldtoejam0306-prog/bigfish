$ErrorActionPreference = 'Stop'
[Console]::InputEncoding = [Text.Encoding]::UTF8
$synth = $null
$output = $null
try {
    $task = [Console]::In.ReadToEnd() | ConvertFrom-Json
    $text = [string]$task.text
    if ([string]::IsNullOrWhiteSpace($text) -or $text.Length -gt 1000 -or [Text.Encoding]::UTF8.GetByteCount($text) -gt 4000) { exit 2 }
    Add-Type -AssemblyName System.Speech
    $synth = [System.Speech.Synthesis.SpeechSynthesizer]::new()
    $chosen = $null
    foreach ($voice in $synth.GetInstalledVoices()) {
        if ($voice.Enabled -and $voice.VoiceInfo.Culture.Name -eq 'zh-CN' -and $voice.VoiceInfo.Description -eq 'Microsoft Huihui Desktop - Chinese (Simplified)') { $chosen = $voice.VoiceInfo; break }
    }
    if ($null -eq $chosen) { exit 7 }
    # COM's descriptive label is not necessarily the .NET voice Name.
    $synth.SelectVoice($chosen.Name)
    if ($synth.Voice.Name -ne $chosen.Name -or $synth.Voice.Culture.Name -ne 'zh-CN') { exit 7 }
    $format = [System.Speech.AudioFormat.SpeechAudioFormatInfo]::new(16000, [System.Speech.AudioFormat.AudioBitsPerSample]::Sixteen, [System.Speech.AudioFormat.AudioChannel]::Mono)
    $output = [Console]::OpenStandardOutput()
    # Raw PCM, not a WAV file. No default audio device is selected. The parent
    # can stop this exact child; no execution policy or other host is changed.
    $synth.SetOutputToAudioStream($output, $format)
    $synth.Speak($text)
    $output.Flush()
} catch {
    [Console]::Error.WriteLine('Local PCM synthesis unavailable.')
    exit 1
} finally {
    if ($null -ne $synth) { $synth.SetOutputToNull(); $synth.Dispose() }
    if ($null -ne $output) { $output.Dispose() }
}
