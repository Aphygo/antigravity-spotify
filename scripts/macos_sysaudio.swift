import AudioToolbox
import CoreAudio
import Foundation

guard CommandLine.arguments.count >= 3,
      let seconds = Double(CommandLine.arguments[2]),
      seconds > 0 else {
    fputs("Usage: macos_sysaudio <output.wav> <seconds>\n", stderr)
    exit(2)
}

let outputPath = CommandLine.arguments[1]
let outputURL = URL(fileURLWithPath: outputPath)

@available(macOS 14.2, *)
func recordSystemAudio(to url: URL, duration: Double) throws {
    let tapDesc = CATapDescription(stereoGlobalTapButExcludeProcesses: [])
    tapDesc.muteBehavior = .unmuted
    tapDesc.name = "AntigravitySpotifyEarTap"

    var tapID: AudioObjectID = kAudioObjectUnknown
    var status = AudioHardwareCreateProcessTap(tapDesc, &tapID)
    guard status == noErr, tapID != kAudioObjectUnknown else {
        throw NSError(domain: "CoreAudio", code: Int(status), userInfo: [
            NSLocalizedDescriptionKey: "AudioHardwareCreateProcessTap failed (\(status))"
        ])
    }
    defer {
        AudioHardwareDestroyProcessTap(tapID)
    }

    // Read tap format (AudioStreamBasicDescription)
    var propAddr = AudioObjectPropertyAddress(
        mSelector: kAudioTapPropertyFormat,
        mScope: kAudioObjectPropertyScopeGlobal,
        mElement: kAudioObjectPropertyElementMain
    )
    var asbd = AudioStreamBasicDescription()
    var asbdSize = UInt32(MemoryLayout<AudioStreamBasicDescription>.size)
    status = AudioObjectGetPropertyData(tapID, &propAddr, 0, nil, &asbdSize, &asbd)
    guard status == noErr else {
        throw NSError(domain: "CoreAudio", code: Int(status), userInfo: [
            NSLocalizedDescriptionKey: "Failed to read kAudioTapPropertyFormat (\(status))"
        ])
    }

    // Read tap UUID string
    var uidAddr = AudioObjectPropertyAddress(
        mSelector: kAudioTapPropertyUID,
        mScope: kAudioObjectPropertyScopeGlobal,
        mElement: kAudioObjectPropertyElementMain
    )
    var tapUIDRef: Unmanaged<CFString>?
    var uidSize = UInt32(MemoryLayout<CFString?>.size)
    status = AudioObjectGetPropertyData(tapID, &uidAddr, 0, nil, &uidSize, &tapUIDRef)
    guard status == noErr, let tapUID = tapUIDRef?.takeRetainedValue() else {
        throw NSError(domain: "CoreAudio", code: Int(status), userInfo: [
            NSLocalizedDescriptionKey: "Failed to read kAudioTapPropertyUID (\(status))"
        ])
    }

    // Create private aggregate device wrapping the tap
    let aggUID = "com.aphygo.antigravity-spotify.tap-\(UUID().uuidString)"
    let tapList: [[String: Any]] = [
        [
            kAudioSubTapUIDKey as String: tapUID as String,
            kAudioSubTapDriftCompensationKey as String: true,
        ]
    ]
    let aggDesc: [String: Any] = [
        kAudioAggregateDeviceNameKey as String: "AntigravitySpotifyEar",
        kAudioAggregateDeviceUIDKey as String: aggUID,
        kAudioAggregateDeviceIsPrivateKey as String: true,
        kAudioAggregateDeviceIsStackedKey as String: false,
        kAudioAggregateDeviceTapAutoStartKey as String: true,
        kAudioAggregateDeviceTapListKey as String: tapList,
    ]

    var aggDeviceID: AudioObjectID = kAudioObjectUnknown
    status = AudioHardwareCreateAggregateDevice(aggDesc as CFDictionary, &aggDeviceID)
    guard status == noErr, aggDeviceID != kAudioObjectUnknown else {
        throw NSError(domain: "CoreAudio", code: Int(status), userInfo: [
            NSLocalizedDescriptionKey: "AudioHardwareCreateAggregateDevice failed (\(status))"
        ])
    }
    defer {
        AudioHardwareDestroyAggregateDevice(aggDeviceID)
    }

    // Output 16-bit interleaved PCM WAV format
    var clientASBD = AudioStreamBasicDescription(
        mSampleRate: asbd.mSampleRate > 0 ? asbd.mSampleRate : 48000,
        mFormatID: kAudioFormatLinearPCM,
        mFormatFlags: kAudioFormatFlagIsSignedInteger | kAudioFormatFlagIsPacked,
        mBytesPerPacket: 4,
        mFramesPerPacket: 1,
        mBytesPerFrame: 4,
        mChannelsPerFrame: 2,
        mBitsPerChannel: 16,
        mReserved: 0
    )

    var extFile: ExtAudioFileRef?
    status = ExtAudioFileCreateWithURL(
        url as CFURL,
        kAudioFileWAVEType,
        &clientASBD,
        nil,
        AudioFileFlags.eraseFile.rawValue,
        &extFile
    )
    guard status == noErr, let audioFile = extFile else {
        throw NSError(domain: "AudioToolbox", code: Int(status), userInfo: [
            NSLocalizedDescriptionKey: "ExtAudioFileCreateWithURL failed (\(status))"
        ])
    }
    defer {
        ExtAudioFileDispose(audioFile)
    }

    var inputASBD = asbd
    status = ExtAudioFileSetProperty(
        audioFile,
        kExtAudioFileProperty_ClientDataFormat,
        UInt32(MemoryLayout<AudioStreamBasicDescription>.size),
        &inputASBD
    )
    guard status == noErr else {
        throw NSError(domain: "AudioToolbox", code: Int(status), userInfo: [
            NSLocalizedDescriptionKey: "ExtAudioFileSetProperty ClientDataFormat failed (\(status))"
        ])
    }

    let lock = NSLock()
    var ioProcID: AudioDeviceIOProcID?
    let ioQueue = DispatchQueue(label: "com.aphygo.antigravity-spotify.io")

    status = AudioDeviceCreateIOProcIDWithBlock(&ioProcID, aggDeviceID, ioQueue) {
        _, inInputData, _, _, _ in
        let ablPtr = UnsafeMutableAudioBufferListPointer(UnsafeMutablePointer(mutating: inInputData))
        guard let firstBuf = ablPtr.first, firstBuf.mDataByteSize > 0 else { return }
        let bytesPerFrame = max(1, Int(inputASBD.mBytesPerFrame))
        let frameCount = UInt32(Int(firstBuf.mDataByteSize) / bytesPerFrame)
        guard frameCount > 0 else { return }
        lock.lock()
        _ = ExtAudioFileWrite(audioFile, frameCount, inInputData)
        lock.unlock()
    }
    guard status == noErr, let procID = ioProcID else {
        throw NSError(domain: "CoreAudio", code: Int(status), userInfo: [
            NSLocalizedDescriptionKey: "AudioDeviceCreateIOProcIDWithBlock failed (\(status))"
        ])
    }

    status = AudioDeviceStart(aggDeviceID, procID)
    guard status == noErr else {
        AudioDeviceDestroyIOProcID(aggDeviceID, procID)
        throw NSError(domain: "CoreAudio", code: Int(status), userInfo: [
            NSLocalizedDescriptionKey: "AudioDeviceStart failed (\(status))"
        ])
    }

    Thread.sleep(forTimeInterval: duration)

    AudioDeviceStop(aggDeviceID, procID)
    AudioDeviceDestroyIOProcID(aggDeviceID, procID)
    lock.lock()
    lock.unlock()
}

if #available(macOS 14.2, *) {
    do {
        try recordSystemAudio(to: outputURL, duration: seconds)
    } catch {
        fputs("ERROR: \(error.localizedDescription)\n", stderr)
        exit(1)
    }
} else {
    fputs("ERROR: CoreAudio Process Tap requires macOS 14.2+\n", stderr)
    exit(1)
}
