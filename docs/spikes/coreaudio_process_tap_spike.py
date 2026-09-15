"""Spike (2026-09-13): capture system audio via a Core Audio process tap (macOS 14.2+) and read it through
sounddevice/PortAudio — proves BlackHole can be replaced. Run: .venv/bin/python docs/spikes/coreaudio_process_tap_spike.py [global|teams <pid>] [x] [out.wav]
NOSUB=1 omits the output-device binding. See the conversation notes / ADR for results."""
import ctypes, signal, subprocess, sys, time, uuid
import numpy as np
import objc
from Foundation import NSDictionary, NSArray, NSUUID

signal.alarm(60)  # guard against a TCC prompt we can't answer

ca = ctypes.CDLL("/System/Library/Frameworks/CoreAudio.framework/CoreAudio")
ca.AudioHardwareCreateProcessTap.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32)]
ca.AudioHardwareDestroyProcessTap.argtypes = [ctypes.c_uint32]
ca.AudioHardwareCreateAggregateDevice.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32)]
ca.AudioHardwareDestroyAggregateDevice.argtypes = [ctypes.c_uint32]

objc.loadBundle("CoreAudio", globals(), bundle_path="/System/Library/Frameworks/CoreAudio.framework")
CATapDescription = objc.lookUpClass("CATapDescription")

def fourcc(s): return int.from_bytes(s.encode(), "big")

class AudioObjectPropertyAddress(ctypes.Structure):
    _fields_ = [("mSelector", ctypes.c_uint32), ("mScope", ctypes.c_uint32), ("mElement", ctypes.c_uint32)]

def default_output_uid():
    addr = AudioObjectPropertyAddress(fourcc("dOut"), fourcc("glob"), 0)
    dev = ctypes.c_uint32(0); size = ctypes.c_uint32(4)
    assert ca.AudioObjectGetPropertyData(1, ctypes.byref(addr), 0, None, ctypes.byref(size), ctypes.byref(dev)) == 0
    addr = AudioObjectPropertyAddress(fourcc("uid "), fourcc("glob"), 0)
    ref = ctypes.c_void_p(0); size = ctypes.c_uint32(8)
    assert ca.AudioObjectGetPropertyData(dev.value, ctypes.byref(addr), 0, None, ctypes.byref(size), ctypes.byref(ref)) == 0
    return str(objc.objc_object(c_void_p=ref.value)), dev.value

out_uid, out_dev = default_output_uid()
print("default output:", out_uid, out_dev)

mode = sys.argv[1] if len(sys.argv) > 1 else "global"
if mode == "teams":
    # Tap only a specific process: find its AudioObjectID via kAudioHardwarePropertyTranslatePIDToProcessObject
    pid = int(sys.argv[2])
    addr = AudioObjectPropertyAddress(fourcc("id2p"), fourcc("glob"), 0)
    obj = ctypes.c_uint32(0); size = ctypes.c_uint32(4); pid_c = ctypes.c_int32(pid)
    st = ca.AudioObjectGetPropertyData(1, ctypes.byref(addr), 4, ctypes.byref(pid_c), ctypes.byref(size), ctypes.byref(obj))
    print("pid->object status", st, "object", obj.value)
    desc = CATapDescription.alloc().initStereoMixdownOfProcesses_(NSArray.arrayWithArray_([obj.value]))
else:
    desc = CATapDescription.alloc().initStereoGlobalTapButExcludeProcesses_(NSArray.array())
desc.setName_("notetaker-spike")
desc.setMuteBehavior_(0)  # unmuted: the user still hears everything
tap_uuid = str(desc.UUID().UUIDString())
tap_id = ctypes.c_uint32(0)
status = ca.AudioHardwareCreateProcessTap(objc.pyobjc_id(desc), ctypes.byref(tap_id))
print("create tap status", status, "tap id", tap_id.value)
if status != 0:
    sys.exit(f"tap failed: {status} ({status.to_bytes(4,'big',signed=True)})")

import os
agg = {
    "name": "notetaker-spike-agg",
    "uid": str(uuid.uuid4()),
    "private": True,
    "tapautostart": True,
    "taps": [{"uid": tap_uuid, "drift": True}],
}
if not os.environ.get("NOSUB"):
    agg["master"] = out_uid
    agg["subdevices"] = [{"uid": out_uid}]
print("aggregate keys:", sorted(agg))
agg_desc = NSDictionary.dictionaryWithDictionary_(agg)
agg_id = ctypes.c_uint32(0)
status = ca.AudioHardwareCreateAggregateDevice(objc.pyobjc_id(agg_desc), ctypes.byref(agg_id))
print("create aggregate status", status, "agg id", agg_id.value)
try:
    import sounddevice as sd
    sd._terminate(); sd._initialize()  # re-enumerate so PortAudio sees the new device
    idx = next((i for i, d in enumerate(sd.query_devices()) if d["name"] == "notetaker-spike-agg"), None)
    print("portaudio sees aggregate at index", idx, sd.query_devices(idx) if idx is not None else "")
    if idx is None:
        sys.exit("aggregate not visible to PortAudio")
    player = subprocess.Popen(["say", "-r", "170", "this is the meeting audio being tapped by notetaker"]) if mode != "teams" else None
    frames = sd.rec(int(3 * 16000), samplerate=16000, channels=1, dtype="int16", device=idx)
    sd.wait()
    if player: player.wait()
    peak = int(np.abs(frames).max())
    print("captured peak", peak, "->", "AUDIO CAPTURED" if peak > 500 else "silent")
    import wave
    with wave.open(sys.argv[3] if len(sys.argv) > 3 else "/tmp/notetaker_tap_spike.wav", "wb") as wf:
        wf.setnchannels(1); wf.setsampwidth(2); wf.setframerate(16000); wf.writeframes(frames.tobytes())
finally:
    print("destroy aggregate", ca.AudioHardwareDestroyAggregateDevice(agg_id.value))
    print("destroy tap", ca.AudioHardwareDestroyProcessTap(tap_id.value))
