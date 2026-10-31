import AVFoundation
import CoreAudio
import Observation
import os

// records a call: your mic on one channel and everything the mac plays on the other, the way
// audio hijack does, so the server splits it by channel as it does those recordings.
//
// the mac's audio comes from a core audio process tap, which asks for the "system audio
// recording only" permission rather than screen recording. the mic and the tap run as two
// devices, since one holding both stalls, mic included, whenever the tap does: while the
// permission prompt is up, when it's denied, and with airpods while nothing plays. the mic's
// io writes the file, taking the call's samples as they've arrived and silence when there are
// none. samples are written to a 16-bit caf in the library's recordings folder, so a crash or
// quit loses nothing, then compressed to m4a when the recording stops. when the mac's input
// or output changes mid-call, such as airpods connecting, the recording carries on with it.
@Observable @MainActor
final class Recorder {
    private(set) var startedAt: Date?
    // what the meeting is called, editable while recording
    var name = ""
    // 0 to 1, for the meters
    private(set) var micLevel: Float = 0
    private(set) var callLevel: Float = 0
    // the mic being recorded, which can change mid-call
    private(set) var micName = ""
    // what's wrong when nothing is being heard, such as a permission not given
    private(set) var problem: String?
    private var session: RecordingSession?
    private var meterTask: Task<Void, Never>?

    static let folder = Server.appSupport.appending(path: "recordings")

    var isRecording: Bool { session != nil }

    // micOnRight puts the mic on the right channel, matching the mic channel setting
    func start(micOnRight: Bool) throws {
        guard session == nil else { return }
        try FileManager.default.createDirectory(at: Self.folder, withIntermediateDirectories: true)
        let date = Date()
        name = "Call at \(date.formatted(date: .omitted, time: .shortened).replacingOccurrences(of: ":", with: "."))"
        // the local date, since iso8601 on its own is utc and gives tomorrow's in the evening
        let day = date.formatted(Date.ISO8601FormatStyle(timeZone: .current).year().month().day())
        let file = Self.folder.appending(path: "\(day) - \(UUID().uuidString.prefix(8)).caf")
        session = try RecordingSession(file: file, micOnRight: micOnRight)
        startedAt = date
        meterTask = Task { [weak self] in
            while let self, let session = self.session, !Task.isCancelled {
                (self.micLevel, self.callLevel) = session.levels()
                if self.micName != session.micName { self.micName = session.micName }
                let problem = session.problem(after: -date.timeIntervalSinceNow)
                if problem != self.problem {
                    Server.log(problem.map { "while recording: \($0)" } ?? "the recording hears audio now")
                    self.problem = problem
                }
                try? await Task.sleep(for: .milliseconds(100))
            }
        }
    }

    // stops and returns the finished caf
    @discardableResult
    func stop() -> URL? {
        guard let session else { return nil }
        meterTask?.cancel()
        session.stop()
        self.session = nil
        startedAt = nil
        problem = nil
        micLevel = 0
        callLevel = 0
        return session.file
    }

    // the recording as a stereo aac m4a named "<date> - <name>.m4a" beside it, which the
    // server takes as an upload. a 16-bit caf is about 700 MB an hour, the m4a about 60.
    // nil when nothing was recorded, as when the app quit just after a recording started
    nonisolated static func compress(_ caf: URL, name: String) throws -> URL? {
        let input = try AVAudioFile(forReading: caf)
        guard input.length > 0 else { return nil }
        let date = caf.lastPathComponent.prefix(10)
        let safe = name.replacingOccurrences(of: "/", with: "-").replacingOccurrences(of: ":", with: ".")
        let m4a = caf.deletingLastPathComponent().appending(path: "\(date) - \(safe).m4a")
        try? FileManager.default.removeItem(at: m4a)
        let format = input.processingFormat
        let output = try AVAudioFile(forWriting: m4a, settings: [
            AVFormatIDKey: kAudioFormatMPEG4AAC,
            AVSampleRateKey: format.sampleRate,
            AVNumberOfChannelsKey: format.channelCount,
            AVEncoderBitRateKey: 128_000,
        ], commonFormat: format.commonFormat, interleaved: format.isInterleaved)
        guard let buffer = AVAudioPCMBuffer(pcmFormat: format, frameCapacity: AVAudioFrameCount(format.sampleRate)) else {
            throw RecorderError("Couldn't compress the recording")
        }
        while input.framePosition < input.length {
            try input.read(into: buffer)
            if buffer.frameLength == 0 { break }
            try output.write(from: buffer)
        }
        return m4a
    }
}

// the system audio permission, which has no public way to ask about. tcc's preflight is what
// apple's process tap sample uses, and if it's ever gone a slow start of the tap stands in for the prompt
enum CallAudio {
    case allowed, denied, notAsked

    private typealias Preflight = @convention(c) (CFString, CFDictionary?) -> Int
    private static let preflight: Preflight? = {
        guard let tcc = dlopen("/System/Library/PrivateFrameworks/TCC.framework/Versions/A/TCC", RTLD_NOW),
              let symbol = dlsym(tcc, "TCCAccessPreflight") else { return nil }
        return unsafeBitCast(symbol, to: Preflight.self)
    }()

    static func permission() -> CallAudio? {
        guard let preflight else { return nil }
        switch preflight("kTCCServiceAudioCapture" as CFString, nil) {
        case 0: return .allowed
        case 1: return .denied
        default: return .notAsked
        }
    }
}

struct RecorderError: LocalizedError {
    let errorDescription: String?
    init(_ message: String) { errorDescription = message }
}

// one recording's core audio objects and file. each device's io runs on its own queue, and
// the file is only written on the mic's. starting and switching devices happen on control
// queues, since stopping a device waits for its io block to finish, and starting the tap waits
// for the system audio permission prompt to be answered
private final class RecordingSession: @unchecked Sendable {
    let file: URL
    private let micOnRight: Bool
    private var tapID = AudioObjectID(kAudioObjectUnknown)
    private var tapUID = ""
    private var tapDescription: CATapDescription?
    private var excluded: [AudioObjectID] = []
    private var micDevice = AudioObjectID(kAudioObjectUnknown)
    private var micProc: AudioDeviceIOProcID?
    private var callDevice = AudioObjectID(kAudioObjectUnknown)
    private var callProc: AudioDeviceIOProcID?
    private var output: AVAudioFile?
    private var inputListener: AudioObjectPropertyListenerBlock?
    private var outputListener: AudioObjectPropertyListenerBlock?
    private var processListener: AudioObjectPropertyListenerBlock?
    private let micQueue = DispatchQueue(label: "wemmick.recorder.mic", qos: .userInteractive)
    private let callQueue = DispatchQueue(label: "wemmick.recorder.call", qos: .userInteractive)
    private let micControl = DispatchQueue(label: "wemmick.recorder.mic-control")
    private let callControl = DispatchQueue(label: "wemmick.recorder.call-control")
    // the call's samples at the file's rate, waiting for the mic's io to take them
    private let callSamples = OSAllocatedUnfairLock(initialState: [Float]())
    private let peaks = OSAllocatedUnfairLock(initialState: (mic: Float(0), call: Float(0)))
    // the loudest each side got over the whole recording, for the log
    private let loudest = OSAllocatedUnfairLock(initialState: (mic: Float(0), call: Float(0)))
    // whether the mic has delivered anything yet
    private let received = OSAllocatedUnfairLock(initialState: false)
    // false when the mac's audio isn't allowed, and only the mic is recorded
    private let callOn = OSAllocatedUnfairLock(initialState: true)
    // when the call side last started, and whether it's delivered anything since. a tap
    // without the permission delivers nothing at all, which is the only sign of a "don't
    // allow" at the prompt, since macos keeps saying it hasn't asked. but one with it allowed
    // delivers nothing either while nothing plays, so it's only a maybe
    private let answeredPrompt = OSAllocatedUnfairLock(initialState: false)
    private let callStarted = OSAllocatedUnfairLock<Date?>(initialState: nil)
    private let callReceived = OSAllocatedUnfairLock(initialState: false)
    // true while the tap waits on the permission prompt
    private let callStarting = OSAllocatedUnfairLock(initialState: false)
    private let mic = OSAllocatedUnfairLock(initialState: "")

    var micName: String { mic.withLock { $0 } }

    // everything is written at 48 khz, the mic and the call converted to it as needed
    private static let fileFormat = AVAudioFormat(commonFormat: .pcmFormatFloat32, sampleRate: 48_000, channels: 2, interleaved: false)!
    private static let monoFormat = AVAudioFormat(commonFormat: .pcmFormatFloat32, sampleRate: 48_000, channels: 1, interleaved: false)!
    // the call's samples kept waiting at most, past which the oldest go. the two devices run
    // on their own clocks, and the mic stops while it switches, so the call runs ahead
    private static let mostWaiting = 24_000
    private static let keptWaiting = 4_800
    // seconds the call side waits for the mic to send sound before starting anyway
    private static let micWait: TimeInterval = 15

    private static let defaultInputAddress = AudioObjectPropertyAddress(
        mSelector: kAudioHardwarePropertyDefaultInputDevice,
        mScope: kAudioObjectPropertyScopeGlobal, mElement: kAudioObjectPropertyElementMain)
    private static let defaultOutputAddress = AudioObjectPropertyAddress(
        mSelector: kAudioHardwarePropertyDefaultOutputDevice,
        mScope: kAudioObjectPropertyScopeGlobal, mElement: kAudioObjectPropertyElementMain)
    private static let processListAddress = AudioObjectPropertyAddress(
        mSelector: kAudioHardwarePropertyProcessObjectList,
        mScope: kAudioObjectPropertyScopeGlobal, mElement: kAudioObjectPropertyElementMain)

    // audio hijack, recording the call as a backup, plays what it captures back out a few
    // buffers late, so the tap would hear the call twice, 80 ms or so apart
    private static let excludedBundlePrefixes = ["com.rogueamoeba.audiohijack"]

    init(file: URL, micOnRight: Bool) throws {
        self.file = file
        self.micOnRight = micOnRight
        let permission = CallAudio.permission()
        Server.log("system audio permission: \(permission.map { "\($0)" } ?? "unknown")")
        do {
            output = try AVAudioFile(forWriting: file, settings: [
                AVFormatIDKey: kAudioFormatLinearPCM, AVSampleRateKey: Self.fileFormat.sampleRate, AVNumberOfChannelsKey: 2,
                AVLinearPCMBitDepthKey: 16, AVLinearPCMIsFloatKey: false, AVLinearPCMIsBigEndianKey: false,
            ], commonFormat: .pcmFormatFloat32, interleaved: false)
            try connectMic(to: try Self.defaultInputDevice())
        } catch {
            Server.log("recorder couldn't start: \(error.localizedDescription)")
            stop()
            throw error
        }
        watchInput()
        guard permission != .denied else {
            Server.log("not allowed to record the Mac's audio, recording the mic alone")
            callOn.withLock { $0 = false }
            return
        }
        callControl.async { [self] in
            // airpods send silence for ten seconds or so when their output starts along with
            // their mic, rather than a second or two, so the call side waits for the mic's
            // first sound, or a few seconds of silence
            let opened = Date()
            while loudest.withLock({ $0.mic }) <= 1e-5, opened.timeIntervalSinceNow > -Self.micWait {
                Thread.sleep(forTimeInterval: 0.05)
            }
            Server.log(String(format: "the mic's first sound came after %.1f s", -opened.timeIntervalSinceNow))
            let asked = Date()
            callStarting.withLock { $0 = true }
            startCall()
            callStarting.withLock { $0 = false }
            // a tap started while the permission prompt was up hears nothing even once it's
            // allowed, so it's started again. without asking macos, a slow start is the prompt
            let prompted = permission == .notAsked || (permission == nil && asked.timeIntervalSinceNow < -0.5)
            guard prompted else { return }
            // macos takes a moment to note the answer after the prompt closes
            var answer = CallAudio.permission()
            for _ in 0..<20 where answer == .notAsked {
                Thread.sleep(forTimeInterval: 0.1)
                answer = CallAudio.permission()
            }
            if answer == .denied {
                Server.log("not allowed to record the Mac's audio at the prompt, recording the mic alone")
                stopCall()
                callOn.withLock { $0 = false }
            } else {
                Server.log("answered the prompt about recording the Mac's audio, starting it again")
                answeredPrompt.withLock { $0 = true }
                stopCall()
                startCall()
            }
        }
        watchOutput()
        watchProcesses()
    }

    // everything the mac plays, mixed to stereo, and left playing as usual
    private func makeTap() throws {
        excluded = Self.excludedProcesses()
        let description = CATapDescription(stereoGlobalTapButExcludeProcesses: excluded)
        description.uuid = UUID()
        description.name = "Wemmick call audio"
        description.isPrivate = true
        description.muteBehavior = .unmuted
        try check(AudioHardwareCreateProcessTap(description, &tapID), "Couldn't start recording the Mac's audio")
        tapUID = description.uuid.uuidString
        tapDescription = description
    }

    // an app only gets a process object once it uses audio, so audio hijack started after the
    // recording is left out when it appears
    private func watchProcesses() {
        let block: AudioObjectPropertyListenerBlock = { [weak self] _, _ in
            guard let self, let description = self.tapDescription, self.tapID != kAudioObjectUnknown else { return }
            let now = Self.excludedProcesses()
            guard now != self.excluded else { return }
            self.excluded = now
            description.processes = now
            var address = AudioObjectPropertyAddress(mSelector: kAudioTapPropertyDescription,
                                                     mScope: kAudioObjectPropertyScopeGlobal, mElement: kAudioObjectPropertyElementMain)
            var value = description
            AudioObjectSetPropertyData(self.tapID, &address, 0, nil, UInt32(MemoryLayout<CATapDescription>.size), &value)
        }
        var address = Self.processListAddress
        if AudioObjectAddPropertyListenerBlock(AudioObjectID(kAudioObjectSystemObject), &address, callControl, block) == noErr {
            processListener = block
        }
    }

    private static func excludedProcesses() -> [AudioObjectID] {
        var address = processListAddress
        var size = UInt32(0)
        let system = AudioObjectID(kAudioObjectSystemObject)
        guard AudioObjectGetPropertyDataSize(system, &address, 0, nil, &size) == noErr, size > 0 else { return [] }
        var processes = [AudioObjectID](repeating: 0, count: Int(size) / MemoryLayout<AudioObjectID>.size)
        guard AudioObjectGetPropertyData(system, &address, 0, nil, &size, &processes) == noErr else { return [] }
        return processes.filter { process in
            let bundle = string(kAudioProcessPropertyBundleID, of: process) ?? ""
            return excludedBundlePrefixes.contains { bundle.hasPrefix($0) }
        }.sorted()
    }

    // a private aggregate device of the mic alone, and the io block writing the file from it
    private func connectMic(to device: AudioObjectID) throws {
        let uid = try Self.uid(of: device)
        let channels = Self.inputChannels(of: device)
        guard channels > 0 else { throw RecorderError("There's no microphone to record from") }
        let aggregate: [String: Any] = [
            kAudioAggregateDeviceNameKey: "Wemmick Mic",
            kAudioAggregateDeviceUIDKey: UUID().uuidString,
            kAudioAggregateDeviceMainSubDeviceKey: uid,
            kAudioAggregateDeviceIsPrivateKey: true,
            kAudioAggregateDeviceIsStackedKey: false,
            kAudioAggregateDeviceSubDeviceListKey: [[kAudioSubDeviceUIDKey: uid]],
        ]
        try check(AudioHardwareCreateAggregateDevice(aggregate as CFDictionary, &micDevice), "Couldn't set up the recorder")
        let rate = Self.rate(of: micDevice)
        guard let format = AVAudioFormat(commonFormat: .pcmFormatFloat32, sampleRate: rate, channels: 1, interleaved: false) else {
            throw RecorderError("Couldn't set up the recording's format")
        }
        let converter = rate == Self.monoFormat.sampleRate ? nil : AVAudioConverter(from: format, to: Self.monoFormat)
        mic.withLock { $0 = Self.name(of: device) }
        let micIndex = micOnRight ? 1 : 0
        try check(AudioDeviceCreateIOProcIDWithBlock(&micProc, micDevice, micQueue) { [weak self] _, input, _, _, _ in
            self?.writeMic(input, format: format, converter: converter, micIndex: micIndex)
        }, "Couldn't start the recorder")
        try check(AudioDeviceStart(micDevice, micProc), "Couldn't start the recorder")
        Server.log("recording from \(Self.name(of: device)), \(channels) channel\(channels == 1 ? "" : "s") at \(Int(rate)) Hz, "
                   + "mic on the \(micOnRight ? "right" : "left")")
    }

    private func disconnectMic() {
        Self.destroy(&micDevice, &micProc)
    }

    // a private aggregate device of the mac's output and the tap, as apple's sample has it,
    // feeding the call's samples to the mic's io
    private func connectCall() throws {
        let outputDevice = try Self.defaultOutputDevice()
        let uid = try Self.uid(of: outputDevice)
        let aggregate: [String: Any] = [
            kAudioAggregateDeviceNameKey: "Wemmick Call Audio",
            kAudioAggregateDeviceUIDKey: UUID().uuidString,
            kAudioAggregateDeviceMainSubDeviceKey: uid,
            kAudioAggregateDeviceIsPrivateKey: true,
            kAudioAggregateDeviceIsStackedKey: false,
            kAudioAggregateDeviceTapAutoStartKey: true,
            kAudioAggregateDeviceSubDeviceListKey: [[kAudioSubDeviceUIDKey: uid]],
            kAudioAggregateDeviceTapListKey: [[kAudioSubTapUIDKey: tapUID, kAudioSubTapDriftCompensationKey: true]],
        ]
        try check(AudioHardwareCreateAggregateDevice(aggregate as CFDictionary, &callDevice), "Couldn't set up recording the Mac's audio")
        let rate = Self.rate(of: callDevice)
        guard let format = AVAudioFormat(commonFormat: .pcmFormatFloat32, sampleRate: rate, channels: 1, interleaved: false) else {
            throw RecorderError("Couldn't set up the recording's format")
        }
        let converter = rate == Self.monoFormat.sampleRate ? nil : AVAudioConverter(from: format, to: Self.monoFormat)
        try check(AudioDeviceCreateIOProcIDWithBlock(&callProc, callDevice, callQueue) { [weak self] _, input, _, _, _ in
            self?.writeCall(input, format: format, converter: converter)
        }, "Couldn't start recording the Mac's audio")
        callReceived.withLock { $0 = false }
        try check(AudioDeviceStart(callDevice, callProc), "Couldn't start recording the Mac's audio")
        callStarted.withLock { $0 = Date() }
        Server.log("recording the Mac's audio through \(Self.name(of: outputDevice)) at \(Int(rate)) Hz")
    }

    // a new tap and the device holding it. runs on the call control queue
    private func startCall() {
        do {
            try makeTap()
            try connectCall()
        } catch {
            Server.log("couldn't record the Mac's audio, recording the mic alone: \(error.localizedDescription)")
        }
    }

    private func stopCall() {
        Self.destroy(&callDevice, &callProc)
        if tapID != kAudioObjectUnknown {
            AudioHardwareDestroyProcessTap(tapID)
            tapID = AudioObjectID(kAudioObjectUnknown)
            tapDescription = nil
        }
        callSamples.withLock { $0.removeAll() }
    }

    private static func destroy(_ device: inout AudioObjectID, _ proc: inout AudioDeviceIOProcID?) {
        guard device != kAudioObjectUnknown else { return }
        if let id = proc {
            AudioDeviceStop(device, id)
            AudioDeviceDestroyIOProcID(device, id)
            proc = nil
        }
        AudioHardwareDestroyAggregateDevice(device)
        device = AudioObjectID(kAudioObjectUnknown)
    }

    // the recording follows the mac's input to a new mic. if that fails, it tries once more
    private func watchInput() {
        let block: AudioObjectPropertyListenerBlock = { [weak self] _, _ in
            guard let self, let device = try? Self.defaultInputDevice() else { return }
            Server.log("the Mac's input changed to \(Self.name(of: device)) during the recording")
            self.disconnectMic()
            do {
                try self.connectMic(to: device)
            } catch {
                Server.log("couldn't switch the recording to it, trying again: \(error.localizedDescription)")
                self.disconnectMic()
                do { try self.connectMic(to: device) } catch { Server.log("couldn't switch again: \(error.localizedDescription)") }
            }
        }
        var address = Self.defaultInputAddress
        if AudioObjectAddPropertyListenerBlock(AudioObjectID(kAudioObjectSystemObject), &address, micControl, block) == noErr {
            inputListener = block
        }
    }

    // and the call side follows its output, since the device it's in goes with the old one
    private func watchOutput() {
        let block: AudioObjectPropertyListenerBlock = { [weak self] _, _ in
            guard let self, self.callOn.withLock({ $0 }), self.callDevice != kAudioObjectUnknown else { return }
            Server.log("the Mac's output changed during the recording")
            self.stopCall()
            self.startCall()
        }
        var address = Self.defaultOutputAddress
        if AudioObjectAddPropertyListenerBlock(AudioObjectID(kAudioObjectSystemObject), &address, callControl, block) == noErr {
            outputListener = block
        }
    }

    // every channel in a buffer list mixed to one, into a mono buffer at the device's rate.
    // from is the first channel to take
    private static func mix(_ input: UnsafePointer<AudioBufferList>, from first: (Int) -> Int, format: AVAudioFormat) -> (AVAudioPCMBuffer, Float)? {
        let buffers = UnsafeMutableAudioBufferListPointer(UnsafeMutablePointer(mutating: input))
        guard let head = buffers.first, head.mNumberChannels > 0 else { return nil }
        let frames = Int(head.mDataByteSize) / (MemoryLayout<Float>.size * Int(head.mNumberChannels))
        guard frames > 0, let pcm = AVAudioPCMBuffer(pcmFormat: format, frameCapacity: AVAudioFrameCount(frames)),
              let out = pcm.floatChannelData?[0] else { return nil }
        pcm.frameLength = AVAudioFrameCount(frames)
        out.update(repeating: 0, count: frames)
        let total = buffers.reduce(0) { $0 + Int($1.mNumberChannels) }
        let start = first(total)
        var channel = 0, mixed = 0
        for buffer in buffers {
            let n = Int(buffer.mNumberChannels)
            defer { channel += n }
            guard let data = buffer.mData?.assumingMemoryBound(to: Float.self) else { continue }
            let count = min(frames, Int(buffer.mDataByteSize) / (MemoryLayout<Float>.size * max(n, 1)))
            for c in 0..<n where channel + c >= start {
                for f in 0..<count { out[f] += data[f * n + c] }
                mixed += 1
            }
        }
        var peak = Float(0)
        for f in 0..<frames {
            if mixed > 1 { out[f] /= Float(mixed) }
            peak = max(peak, abs(out[f]))
        }
        return (pcm, peak)
    }

    private static func convert(_ pcm: AVAudioPCMBuffer, with converter: AVAudioConverter?) -> AVAudioPCMBuffer? {
        guard let converter else { return pcm }
        let capacity = AVAudioFrameCount(Double(pcm.frameLength) * monoFormat.sampleRate / pcm.format.sampleRate) + 32
        guard let converted = AVAudioPCMBuffer(pcmFormat: monoFormat, frameCapacity: capacity) else { return nil }
        var given = false
        converter.convert(to: converted, error: nil) { _, status in
            if given {
                status.pointee = .noDataNow
                return nil
            }
            given = true
            status.pointee = .haveData
            return pcm
        }
        return converted
    }

    // the mic mixed to one channel, written beside as many of the call's samples as have
    // arrived, and silence for the rest
    private func writeMic(_ input: UnsafePointer<AudioBufferList>, format: AVAudioFormat, converter: AVAudioConverter?, micIndex: Int) {
        guard let output, let (raw, peak) = Self.mix(input, from: { _ in 0 }, format: format),
              let mono = Self.convert(raw, with: converter), let micData = mono.floatChannelData?[0] else { return }
        received.withLock { $0 = true }
        let frames = Int(mono.frameLength)
        guard frames > 0, let pcm = AVAudioPCMBuffer(pcmFormat: Self.fileFormat, frameCapacity: AVAudioFrameCount(frames)),
              let channels = pcm.floatChannelData else { return }
        pcm.frameLength = AVAudioFrameCount(frames)
        channels[micIndex].update(from: micData, count: frames)
        let call = channels[1 - micIndex]
        call.update(repeating: 0, count: frames)
        callSamples.withLockUnchecked { samples in
            let n = min(frames, samples.count)
            guard n > 0 else { return }
            samples.withUnsafeBufferPointer { call.update(from: $0.baseAddress!, count: n) }
            samples.removeFirst(n)
        }
        peaks.withLock { $0.mic = max($0.mic, peak) }
        loudest.withLock { $0.mic = max($0.mic, peak) }
        try? output.write(from: pcm)
    }

    // the tap's two channels, after any of the output device's own, mixed to one and left
    // for the mic's io
    private func writeCall(_ input: UnsafePointer<AudioBufferList>, format: AVAudioFormat, converter: AVAudioConverter?) {
        guard let (raw, peak) = Self.mix(input, from: { max(0, $0 - 2) }, format: format),
              let mono = Self.convert(raw, with: converter), let data = mono.floatChannelData?[0] else { return }
        callReceived.withLock { $0 = true }
        let frames = Int(mono.frameLength)
        callSamples.withLockUnchecked { samples in
            samples.append(contentsOf: UnsafeBufferPointer(start: data, count: frames))
            if samples.count > Self.mostWaiting { samples.removeFirst(samples.count - Self.keptWaiting) }
        }
        peaks.withLock { $0.call = max($0.call, peak) }
        loudest.withLock { $0.call = max($0.call, peak) }
    }

    // after a few seconds, a mic delivering nothing, or at exact silence, which a working mic
    // never is, means a permission isn't given or the device isn't working. the call side is
    // often silent for real, so only the mic is judged
    func problem(after seconds: TimeInterval) -> String? {
        if seconds > 1, callStarting.withLock({ $0 }) {
            return "Answer the prompt about recording the Mac's audio to record the call. Your mic is recording."
        }
        guard seconds > 5 else { return nil }
        guard received.withLock({ $0 }) else {
            return "No audio is coming in from the mic. Check Microphone in Privacy & Security."
        }
        guard loudest.withLock({ $0.mic }) > 1e-5 else {
            return "\(micName) is silent. Check Microphone in Privacy & Security, or choose another input."
        }
        guard callOn.withLock({ $0 }) else {
            return "Recording only your mic. To record the call too, allow Wemmick under Screen & System Audio Recording."
        }
        let callSilent = !callReceived.withLock({ $0 }) && (callStarted.withLock({ $0 })?.timeIntervalSinceNow ?? 0) < -3
        if callSilent, answeredPrompt.withLock({ $0 }) {
            return "Nothing from the Mac's audio yet. If you chose Don't Allow, allow Wemmick under Screen & System Audio Recording to record the call."
        }
        return nil
    }

    // the loudest each side has been since last asked, from 0 to 1 on a decibel scale
    func levels() -> (Float, Float) {
        let (mic, call) = peaks.withLock { p in defer { p = (0, 0) }; return p }
        func scale(_ peak: Float) -> Float { max(0, min(1, (20 * log10(max(peak, 1e-6)) + 50) / 50)) }
        return (scale(mic), scale(call))
    }

    // the mic stops and the file closes now. the call side stops once it's done starting,
    // which waits on the permission prompt if it's still up
    func stop() {
        micControl.sync {
            if let inputListener {
                var address = Self.defaultInputAddress
                AudioObjectRemovePropertyListenerBlock(AudioObjectID(kAudioObjectSystemObject), &address, micControl, inputListener)
                self.inputListener = nil
            }
            disconnectMic()
        }
        callControl.async { [self] in
            for (listener, address) in [(outputListener, Self.defaultOutputAddress), (processListener, Self.processListAddress)] {
                guard let listener else { continue }
                var address = address
                AudioObjectRemovePropertyListenerBlock(AudioObjectID(kAudioObjectSystemObject), &address, callControl, listener)
            }
            outputListener = nil
            processListener = nil
            callOn.withLock { $0 = false }
            stopCall()
        }
        // closing the file finishes its header
        let seconds = micQueue.sync {
            defer { output = nil }
            return output.map { Double($0.length) / $0.processingFormat.sampleRate }
        }
        if let seconds {
            // a side that never got above about -100 db heard nothing at all
            let (mic, call) = loudest.withLock { $0 }
            func db(_ peak: Float) -> String { String(format: "%.0f dB", 20 * log10(max(peak, 1e-6))) }
            Server.log("recording stopped after \(Int(seconds)) s, loudest on the mic \(db(mic)), on the call \(db(call))"
                       + (callReceived.withLock { $0 } ? "" : ", and the Mac's audio sent nothing"))
        }
    }

    private func check(_ status: OSStatus, _ message: String) throws {
        if status != noErr { throw RecorderError("\(message) (error \(status))") }
    }

    private static func defaultInputDevice() throws -> AudioObjectID {
        var device = AudioObjectID(kAudioObjectUnknown)
        var size = UInt32(MemoryLayout<AudioObjectID>.size)
        var address = defaultInputAddress
        let status = AudioObjectGetPropertyData(AudioObjectID(kAudioObjectSystemObject), &address, 0, nil, &size, &device)
        guard status == noErr, device != kAudioObjectUnknown else { throw RecorderError("There's no microphone to record from") }
        return device
    }

    private static func defaultOutputDevice() throws -> AudioObjectID {
        var device = AudioObjectID(kAudioObjectUnknown)
        var size = UInt32(MemoryLayout<AudioObjectID>.size)
        var address = defaultOutputAddress
        let status = AudioObjectGetPropertyData(AudioObjectID(kAudioObjectSystemObject), &address, 0, nil, &size, &device)
        guard status == noErr, device != kAudioObjectUnknown else { throw RecorderError("There's no audio output") }
        return device
    }

    private static func rate(of device: AudioObjectID) -> Float64 {
        var rate = Float64(48_000)
        var size = UInt32(MemoryLayout<Float64>.size)
        var address = AudioObjectPropertyAddress(mSelector: kAudioDevicePropertyNominalSampleRate,
                                                 mScope: kAudioObjectPropertyScopeGlobal, mElement: kAudioObjectPropertyElementMain)
        AudioObjectGetPropertyData(device, &address, 0, nil, &size, &rate)
        return rate
    }

    private static func string(_ selector: AudioObjectPropertySelector, of device: AudioObjectID) -> String? {
        var value: Unmanaged<CFString>?
        var size = UInt32(MemoryLayout<Unmanaged<CFString>?>.size)
        var address = AudioObjectPropertyAddress(mSelector: selector, mScope: kAudioObjectPropertyScopeGlobal,
                                                 mElement: kAudioObjectPropertyElementMain)
        guard AudioObjectGetPropertyData(device, &address, 0, nil, &size, &value) == noErr, let value else { return nil }
        return value.takeRetainedValue() as String
    }

    private static func uid(of device: AudioObjectID) throws -> String {
        guard let uid = string(kAudioDevicePropertyDeviceUID, of: device) else { throw RecorderError("Couldn't find the microphone") }
        return uid
    }

    private static func name(of device: AudioObjectID) -> String {
        string(kAudioObjectPropertyName, of: device) ?? "Microphone"
    }

    private static func inputChannels(of device: AudioObjectID) -> Int {
        var address = AudioObjectPropertyAddress(mSelector: kAudioDevicePropertyStreamConfiguration,
                                                 mScope: kAudioObjectPropertyScopeInput, mElement: kAudioObjectPropertyElementMain)
        var size = UInt32(0)
        guard AudioObjectGetPropertyDataSize(device, &address, 0, nil, &size) == noErr, size > 0 else { return 0 }
        let list = UnsafeMutableRawPointer.allocate(byteCount: Int(size), alignment: MemoryLayout<AudioBufferList>.alignment)
        defer { list.deallocate() }
        guard AudioObjectGetPropertyData(device, &address, 0, nil, &size, list) == noErr else { return 0 }
        let buffers = UnsafeMutableAudioBufferListPointer(list.assumingMemoryBound(to: AudioBufferList.self))
        return buffers.reduce(0) { $0 + Int($1.mNumberChannels) }
    }
}
