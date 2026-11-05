$ErrorActionPreference = 'Stop'
try {
    Add-Type -AssemblyName System.Speech
    $speechAssembly = [System.Speech.Recognition.SpeechRecognitionEngine].Assembly.Location
    $systemAssembly = [System.ComponentModel.AsyncCompletedEventArgs].Assembly.Location
    Add-Type -Path (Join-Path $PSScriptRoot 'native_speech_host.cs') -ReferencedAssemblies @($speechAssembly, $systemAssembly)
    exit ([BigFishContinuousSpeechHost]::Run())
} catch {
    # No diagnostic exception or transcript is emitted to the protocol pipe.
    [Console]::Error.WriteLine('Local continuous recognition unavailable.')
    exit 1
}
