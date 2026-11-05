// Loaded only by the reviewed Windows PowerShell -File host after normal
// permission. Library compilation is checked; the runtime host is not executed.
using System;
using System.Globalization;
using System.IO;
using System.Speech.AudioFormat;
using System.Speech.Recognition;
using System.Text;
using System.Threading;

public static class BigFishContinuousSpeechHost
{
    private static readonly object Sync = new object();
    private static readonly ManualResetEvent Done = new ManualResetEvent(false);
    private static long sequence;
    private static int utterance, active;
    private static bool terminal, success;

    private static string Quote(string text)
    {
        var value = new StringBuilder("\"");
        foreach (char ch in text)
        {
            if (ch == '\\' || ch == '"') { value.Append('\\'); value.Append(ch); }
            else if (ch < 32) { value.Append("\\u"); value.Append(((int)ch).ToString("x4", CultureInfo.InvariantCulture)); }
            else value.Append(ch);
        }
        return value.Append('"').ToString();
    }

    // Caller holds Sync; no transcript is written to stderr or a log/file.
    private static void Emit(string kind, string fields)
    {
        Console.Out.WriteLine("{\"seq\":" + (++sequence).ToString(CultureInfo.InvariantCulture) + ",\"type\":" + Quote(kind) + fields + "}");
        Console.Out.Flush();
    }
    private static void Fail(string code)
    {
        lock (Sync)
        {
            if (!terminal) { terminal = true; Emit("error", ",\"code\":" + Quote(code)); }
            Done.Set();
        }
    }
    private static void Detected(object sender, SpeechDetectedEventArgs args)
    {
        lock (Sync)
        {
            if (terminal) return;
            if (active != 0) { Fail("event-order"); return; }
            active = ++utterance;
            Emit("speech", ",\"utterance\":" + active.ToString(CultureInfo.InvariantCulture));
        }
    }
    private static void Recognized(object sender, SpeechRecognizedEventArgs args)
    {
        lock (Sync)
        {
            if (terminal) return;
            if (active == 0 || args.Result == null) { Fail("event-order"); return; }
            string text = args.Result.Text;
            if (String.IsNullOrWhiteSpace(text) || text.Length > 1000 || Encoding.UTF8.GetByteCount(text) > 4000) { Fail("text-limit"); return; }
            foreach (char ch in text) if (ch < 32 && ch != '\n' && ch != '\t') { Fail("text-limit"); return; }
            Emit("final", ",\"utterance\":" + active.ToString(CultureInfo.InvariantCulture) + ",\"text\":" + Quote(text));
            active = 0;
        }
    }
    private static void Rejected(object sender, SpeechRecognitionRejectedEventArgs args)
    {
        lock (Sync)
        {
            if (terminal) return;
            if (active == 0) { Fail("event-order"); return; }
            Emit("rejected", ",\"utterance\":" + active.ToString(CultureInfo.InvariantCulture)); active = 0;
        }
    }
    private static void Completed(object sender, RecognizeCompletedEventArgs args)
    {
        lock (Sync)
        {
            if (terminal) { Done.Set(); return; }
            if (active != 0) { Emit("rejected", ",\"utterance\":" + active.ToString(CultureInfo.InvariantCulture)); active = 0; }
            success = args.Error == null && !args.Cancelled && args.InputStreamEnded;
            terminal = true; Emit("completed", ",\"ok\":" + (success ? "true" : "false")); Done.Set();
        }
    }
    public static int Run()
    {
        Console.OutputEncoding = new UTF8Encoding(false, true);
        try
        {
            using (var audio = Console.OpenStandardInput())
            using (var engine = new SpeechRecognitionEngine(CultureInfo.GetCultureInfo("zh-CN")))
            {
                engine.LoadGrammar(new DictationGrammar());
                engine.InitialSilenceTimeout = TimeSpan.Zero;
                engine.SpeechDetected += Detected;
                engine.SpeechRecognized += Recognized;
                engine.SpeechRecognitionRejected += Rejected;
                engine.RecognizeCompleted += Completed;
                // Handshake means the host accepts PCM, not device/accuracy
                // acceptance. Send before configuring a potentially pulling stream.
                lock (Sync) Emit("ready", ",\"version\":1,\"rate\":16000,\"channels\":1,\"sample_bytes\":2");
                try
                {
                    engine.SetInputToAudioStream(audio, new SpeechAudioFormatInfo(16000, AudioBitsPerSample.Sixteen, AudioChannel.Mono));
                    engine.RecognizeAsync(RecognizeMode.Multiple);
                    if (!Done.WaitOne(TimeSpan.FromMinutes(10))) Fail("session-limit");
                }
                finally
                {
                    try { engine.RecognizeAsyncCancel(); } catch (InvalidOperationException) { }
                    engine.SetInputToNull();
                }
            }
            return success ? 0 : 1;
        }
        catch { Fail("host-unavailable"); return 1; }
    }
}
