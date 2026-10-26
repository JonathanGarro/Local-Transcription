import AVFoundation
import CoreAudio
import Observation
import os

// records a call: your mic on one channel and everything the mac plays on the other, the way
// audio hijack does, so the server splits it by channel as it does those recordings.
//
// the mac's audio comes from a core audio process tap, which asks for the "system audio
// recording only" permission rather than screen recording. the mic and the tap go into one
// private aggregate device, so core audio keeps them in step, with drift compensation on the
// tap. samples are written as they arrive to a 16-bit caf in the library's recordings folder,
// so a crash or quit loses nothing, then compressed to m4a when the recording stops. when the
// mac's input changes mid-call, such as airpods connecting, the recording carries on from the
// new mic.
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
        let file = Self.folder.appending(path: "\(date.formatted(.iso8601.year().month().day())) - \(UUID().uuidString.prefix(8)).caf")
        session = try RecordingSession(file: file, micOnRight: micOnRight)
        startedAt = date
        meterTask = Task { [weak self] in
            while let self, let session = self.session, !Task.isCancelled {
                (self.micLevel, self.callLevel) = session.levels()
                if self.micName != session.micName { self.micName = session.micName }
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
        micLevel = 0
        callLevel = 0
        return session.file
    }

    // the recording as a stereo aac m4a named "<date> - <name>.m4a" beside it, which the
    // server takes as an upload. a 16-bit caf is about 700 MB an hour, the m4a about 60
    nonisolated static func compress(_ caf: URL, name: String) throws -> URL {
        let input = try AVAudioFile(forReading: caf)
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

struct RecorderError: LocalizedError {
    let errorDescription: String?
    init(_ message: String) { errorDescription = message }
}

// one recording's core audio objects and file. the io block runs on its own queue, and the
// file is only written there. switching mics happens on another queue, since stopping a device
// waits for its io block to finish
private final class RecordingSession: @unchecked Sendable {
    let file: URL
    private let micOnRight: Bool
    private var tapID = AudioObjectID(kAudioObjectUnknown)
    private var tapUID = ""
    private var deviceID = AudioObjectID(kAudioObjectUnknown)
    private var procID: AudioDeviceIOProcID?
    private var output: AVAudioFile?
    // the file's format, set by the first mic. a mic at another rate is converted to it
    private var fileFormat: AVAudioFormat?
    private var converter: AVAudioConverter?
    private var listener: AudioObjectPropertyListenerBlock?
    private let queue = DispatchQueue(label: "wemmick.recorder", qos: .userInteractive)
    private let control = DispatchQueue(label: "wemmick.recorder.control")
    private let peaks = OSAllocatedUnfairLock(initialState: (mic: Float(0), call: Float(0)))
    private let mic = OSAllocatedUnfairLock(initialState: "")

    var micName: String { mic.withLock { $0 } }

    private static let defaultInputAddress = AudioObjectPropertyAddress(
        mSelector: kAudioHardwarePropertyDefaultInputDevice,
        mScope: kAudioObjectPropertyScopeGlobal, mElement: kAudioObjectPropertyElementMain)

    init(file: URL, micOnRight: Bool) throws {
        self.file = file
        self.micOnRight = micOnRight
        do {
            try makeTap()
            try connect(to: try Self.defaultInputDevice())
            watchInput()
        } catch {
            stop()
            throw error
        }
    }

    // everything the mac plays, mixed to stereo, and left playing as usual. it outlives a
    // change of mic
    private func makeTap() throws {
        let description = CATapDescription(stereoGlobalTapButExcludeProcesses: [])
        description.uuid = UUID()
        description.name = "Wemmick call audio"
        description.isPrivate = true
        description.muteBehavior = .unmuted
        try check(AudioHardwareCreateProcessTap(description, &tapID), "Couldn't start recording the Mac's audio")
        tapUID = description.uuid.uuidString
    }

    // a private aggregate device of the mic and the tap, and the io block writing from it
    private func connect(to micDevice: AudioObjectID) throws {
        let micUID = try Self.uid(of: micDevice)
        let micChannels = Self.inputChannels(of: micDevice)
        guard micChannels > 0 else { throw RecorderError("There's no microphone to record from") }
        let aggregate: [String: Any] = [
            kAudioAggregateDeviceNameKey: "Wemmick Recorder",
            kAudioAggregateDeviceUIDKey: UUID().uuidString,
            kAudioAggregateDeviceMainSubDeviceKey: micUID,
            kAudioAggregateDeviceIsPrivateKey: true,
            kAudioAggregateDeviceIsStackedKey: false,
            kAudioAggregateDeviceTapAutoStartKey: true,
            kAudioAggregateDeviceSubDeviceListKey: [[kAudioSubDeviceUIDKey: micUID]],
            kAudioAggregateDeviceTapListKey: [[kAudioSubTapUIDKey: tapUID, kAudioSubTapDriftCompensationKey: true]],
        ]
        try check(AudioHardwareCreateAggregateDevice(aggregate as CFDictionary, &deviceID), "Couldn't set up the recorder")

        var rate = Float64(48_000)
        var size = UInt32(MemoryLayout<Float64>.size)
        var address = AudioObjectPropertyAddress(mSelector: kAudioDevicePropertyNominalSampleRate,
                                                 mScope: kAudioObjectPropertyScopeGlobal, mElement: kAudioObjectPropertyElementMain)
        AudioObjectGetPropertyData(deviceID, &address, 0, nil, &size, &rate)
        guard let format = AVAudioFormat(commonFormat: .pcmFormatFloat32, sampleRate: rate, channels: 2, interleaved: false) else {
            throw RecorderError("Couldn't set up the recording's format")
        }
        try queue.sync {
            if output == nil {
                // written as 16-bit, from float samples, one channel each for the mic and the call
                output = try AVAudioFile(forWriting: file, settings: [
                    AVFormatIDKey: kAudioFormatLinearPCM, AVSampleRateKey: rate, AVNumberOfChannelsKey: 2,
                    AVLinearPCMBitDepthKey: 16, AVLinearPCMIsFloatKey: false, AVLinearPCMIsBigEndianKey: false,
                ], commonFormat: .pcmFormatFloat32, interleaved: false)
                fileFormat = format
            }
            converter = fileFormat.flatMap { $0.sampleRate == rate ? nil : AVAudioConverter(from: format, to: $0) }
        }
        mic.withLock { $0 = Self.name(of: micDevice) }
        let micIndex = micOnRight ? 1 : 0
        try check(AudioDeviceCreateIOProcIDWithBlock(&procID, deviceID, queue) { [weak self] _, input, _, _, _ in
            self?.write(input, format: format, micChannels: micChannels, micIndex: micIndex)
        }, "Couldn't start the recorder")
        try check(AudioDeviceStart(deviceID, procID), "Couldn't start the recorder")
    }

    private func disconnect() {
        guard deviceID != kAudioObjectUnknown else { return }
        if let procID {
            AudioDeviceStop(deviceID, procID)
            AudioDeviceDestroyIOProcID(deviceID, procID)
            self.procID = nil
        }
        AudioHardwareDestroyAggregateDevice(deviceID)
        deviceID = AudioObjectID(kAudioObjectUnknown)
    }

    // the recording follows the mac's input to a new mic. if that fails, it goes back to
    // whatever input there is, so the call side keeps recording
    private func watchInput() {
        let block: AudioObjectPropertyListenerBlock = { [weak self] _, _ in
            guard let self, let device = try? Self.defaultInputDevice() else { return }
            self.disconnect()
            if (try? self.connect(to: device)) == nil {
                self.disconnect()
                try? self.connect(to: device)
            }
        }
        var address = Self.defaultInputAddress
        if AudioObjectAddPropertyListenerBlock(AudioObjectID(kAudioObjectSystemObject), &address, control, block) == noErr {
            listener = block
        }
    }

    // the input holds the mic's channels first, then the tap's two. the mic and the call are
    // each mixed to one channel
    private func write(_ input: UnsafePointer<AudioBufferList>, format: AVAudioFormat, micChannels: Int, micIndex: Int) {
        let buffers = UnsafeMutableAudioBufferListPointer(UnsafeMutablePointer(mutating: input))
        guard let output, let first = buffers.first, first.mNumberChannels > 0 else { return }
        let frames = Int(first.mDataByteSize) / (MemoryLayout<Float>.size * Int(first.mNumberChannels))
        guard frames > 0, let pcm = AVAudioPCMBuffer(pcmFormat: format, frameCapacity: AVAudioFrameCount(frames)),
              let channels = pcm.floatChannelData else { return }
        pcm.frameLength = AVAudioFrameCount(frames)
        let mic = channels[micIndex], call = channels[1 - micIndex]
        mic.update(repeating: 0, count: frames)
        call.update(repeating: 0, count: frames)
        var micCount = 0, callCount = 0
        for buffer in buffers {
            guard let data = buffer.mData?.assumingMemoryBound(to: Float.self) else { continue }
            let n = Int(buffer.mNumberChannels)
            let count = min(frames, Int(buffer.mDataByteSize) / (MemoryLayout<Float>.size * max(n, 1)))
            for c in 0..<n {
                let toMic = micCount < micChannels
                let target = toMic ? mic : call
                for f in 0..<count { target[f] += data[f * n + c] }
                if toMic { micCount += 1 } else { callCount += 1 }
            }
        }
        var micPeak = Float(0), callPeak = Float(0)
        for f in 0..<frames {
            if micCount > 1 { mic[f] /= Float(micCount) }
            if callCount > 1 { call[f] /= Float(callCount) }
            micPeak = max(micPeak, abs(mic[f]))
            callPeak = max(callPeak, abs(call[f]))
        }
        let latest = (mic: micPeak, call: callPeak)
        peaks.withLock { $0 = (max($0.mic, latest.mic), max($0.call, latest.call)) }
        guard let converter, let fileFormat else {
            try? output.write(from: pcm)
            return
        }
        let capacity = AVAudioFrameCount(Double(frames) * fileFormat.sampleRate / format.sampleRate) + 32
        guard let converted = AVAudioPCMBuffer(pcmFormat: fileFormat, frameCapacity: capacity) else { return }
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
        try? output.write(from: converted)
    }

    // the loudest each side has been since last asked, from 0 to 1 on a decibel scale
    func levels() -> (Float, Float) {
        let (mic, call) = peaks.withLock { p in defer { p = (0, 0) }; return p }
        func scale(_ peak: Float) -> Float { max(0, min(1, (20 * log10(max(peak, 1e-6)) + 50) / 50)) }
        return (scale(mic), scale(call))
    }

    func stop() {
        if let listener {
            var address = Self.defaultInputAddress
            AudioObjectRemovePropertyListenerBlock(AudioObjectID(kAudioObjectSystemObject), &address, control, listener)
            self.listener = nil
        }
        control.sync { disconnect() }
        if tapID != kAudioObjectUnknown {
            AudioHardwareDestroyProcessTap(tapID)
            tapID = AudioObjectID(kAudioObjectUnknown)
        }
        // closing the file finishes its header
        queue.sync { output = nil }
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
