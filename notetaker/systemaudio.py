"""System-audio capture via Core Audio process taps (macOS 14.2+).

A process tap sees every app's audio *before* it is routed to an output
device, so it needs no BlackHole driver, no admin password, no reboot and
no Multi-Output Device — and headphones stop mattering. The tap is wrapped
in a private aggregate device that PortAudio (sounddevice) can open like
any input device, so the rest of the recorder is unchanged.

The C API takes an Objective-C `CATapDescription` and a CoreFoundation
dictionary; both come from PyObjC's Cocoa package, which `rumps` already
requires. Everything Core Audio is loaded lazily through `_bindings()` so
tests can replace it and non-macOS imports don't fail.

Requires the "System Audio Recording Only" privacy permission; macOS
prompts on first use when a GUI app owns the process (Terminal, VS Code).
"""

import ctypes
import platform
import uuid
from dataclasses import dataclass, field

REQUIRED_MACOS = (14, 2)
TAP_NAME = "notetaker system audio"
AGGREGATE_NAME = "notetaker-system-audio"
_SYSTEM_OBJECT = 1  # kAudioObjectSystemObject
_MUTE_BEHAVIOR_UNMUTED = 0  # CATapUnmuted: the user keeps hearing the audio


class SystemAudioTapError(Exception):
    """Creating or wiring a process tap failed; str(exc) is user-facing."""


def _fourcc(code: str) -> int:
    return int.from_bytes(code.encode("ascii"), "big")


class _PropertyAddress(ctypes.Structure):
    _fields_ = [("mSelector", ctypes.c_uint32), ("mScope", ctypes.c_uint32), ("mElement", ctypes.c_uint32)]


class _Bindings:
    """Loads CoreAudio + the PyObjC classes we need. Raises if unavailable."""

    def __init__(self):
        import objc
        from Foundation import NSArray, NSDictionary

        self.objc = objc
        self.NSArray = NSArray
        self.NSDictionary = NSDictionary
        lib = ctypes.CDLL("/System/Library/Frameworks/CoreAudio.framework/CoreAudio")
        lib.AudioHardwareCreateProcessTap.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32)]
        lib.AudioHardwareDestroyProcessTap.argtypes = [ctypes.c_uint32]
        lib.AudioHardwareCreateAggregateDevice.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32)]
        lib.AudioHardwareDestroyAggregateDevice.argtypes = [ctypes.c_uint32]
        lib.AudioObjectGetPropertyDataSize.argtypes = [
            ctypes.c_uint32, ctypes.c_void_p, ctypes.c_uint32, ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32),
        ]
        lib.AudioObjectGetPropertyData.argtypes = [
            ctypes.c_uint32, ctypes.c_void_p, ctypes.c_uint32, ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_uint32), ctypes.c_void_p,
        ]
        self.lib = lib
        objc.loadBundle("CoreAudio", {}, bundle_path="/System/Library/Frameworks/CoreAudio.framework")
        self.CATapDescription = objc.lookUpClass("CATapDescription")


_cached_bindings: _Bindings | None = None


def _bindings() -> _Bindings:
    global _cached_bindings
    if _cached_bindings is None:
        _cached_bindings = _Bindings()
    return _cached_bindings


def tap_support_problem() -> str | None:
    """Why process taps can't be used on this machine, or None if they can."""
    if platform.system() != "Darwin":
        return "system_audio: tap requires macOS."
    version = platform.mac_ver()[0]
    try:
        parts = tuple(int(x) for x in version.split(".")[:2])
    except ValueError:
        parts = (0, 0)
    if parts < REQUIRED_MACOS:
        return (
            f"system_audio: tap requires macOS {REQUIRED_MACOS[0]}.{REQUIRED_MACOS[1]}+ (found {version}). "
            "Set system_audio: blackhole in ~/.notetaker/config.yaml to use BlackHole instead."
        )
    try:
        _bindings()
    except Exception as exc:  # missing framework symbol, PyObjC, etc.
        return f"Core Audio process taps are unavailable on this machine: {exc}"
    return None


def _property_uint32_list(b: _Bindings, object_id: int, selector: str) -> list[int]:
    addr = _PropertyAddress(_fourcc(selector), _fourcc("glob"), 0)
    size = ctypes.c_uint32(0)
    if b.lib.AudioObjectGetPropertyDataSize(object_id, ctypes.byref(addr), 0, None, ctypes.byref(size)) != 0:
        return []
    count = size.value // 4
    values = (ctypes.c_uint32 * max(count, 1))()
    if b.lib.AudioObjectGetPropertyData(object_id, ctypes.byref(addr), 0, None, ctypes.byref(size), values) != 0:
        return []
    return list(values)[: size.value // 4]


def _property_string(b: _Bindings, object_id: int, selector: str) -> str | None:
    addr = _PropertyAddress(_fourcc(selector), _fourcc("glob"), 0)
    ref = ctypes.c_void_p(0)
    size = ctypes.c_uint32(ctypes.sizeof(ref))
    if b.lib.AudioObjectGetPropertyData(object_id, ctypes.byref(addr), 0, None, ctypes.byref(size), ctypes.byref(ref)) != 0:
        return None
    if not ref.value:
        return None
    return str(b.objc.objc_object(c_void_p=ref.value))


def find_process_objects(bundle_id: str) -> list[int]:
    """Audio object IDs of every running process with this bundle id
    (kAudioHardwarePropertyProcessObjectList / kAudioProcessPropertyBundleID).
    Empty when the app is not running or has never touched audio."""
    b = _bindings()
    return [
        obj for obj in _property_uint32_list(b, _SYSTEM_OBJECT, "prs#")
        if _property_string(b, obj, "pbid") == bundle_id
    ]


@dataclass
class SystemAudioTap:
    tap_id: int
    aggregate_id: int
    device_name: str
    tapped_bundle_id: str | None = None  # None = every process (global tap)
    fell_back_to_global: bool = False
    _closed: bool = field(default=False, repr=False)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        b = _bindings()
        b.lib.AudioHardwareDestroyAggregateDevice(self.aggregate_id)
        b.lib.AudioHardwareDestroyProcessTap(self.tap_id)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def create_system_audio_tap(process_bundle_id: str | None = None) -> SystemAudioTap:
    """Creates a process tap (all processes, or only `process_bundle_id`
    when that app is running) wrapped in a private aggregate device named
    `AGGREGATE_NAME`. The aggregate is deliberately not bound to any output
    device, so switching between speakers and headphones mid-meeting does
    not affect capture. Caller must `close()` it (or use as a context
    manager) — otherwise the tap lingers until the process exits.
    """
    b = _bindings()
    fell_back = False
    tapped: str | None = None
    if process_bundle_id:
        objects = find_process_objects(process_bundle_id)
        if objects:
            description = b.CATapDescription.alloc().initStereoMixdownOfProcesses_(b.NSArray.arrayWithArray_(objects))
            tapped = process_bundle_id
        else:
            fell_back = True
    if tapped is None:
        description = b.CATapDescription.alloc().initStereoGlobalTapButExcludeProcesses_(b.NSArray.array())
    description.setName_(TAP_NAME)
    description.setMuteBehavior_(_MUTE_BEHAVIOR_UNMUTED)
    tap_uuid = str(description.UUID().UUIDString())

    tap_id = ctypes.c_uint32(0)
    status = b.lib.AudioHardwareCreateProcessTap(b.objc.pyobjc_id(description), ctypes.byref(tap_id))
    if status != 0:
        raise SystemAudioTapError(
            f"could not create a system audio tap (Core Audio status {status}). Grant notetaker "
            "'System Audio Recording' in System Settings > Privacy & Security > Screen & System Audio Recording, "
            "or set system_audio: blackhole in ~/.notetaker/config.yaml."
        )

    aggregate = b.NSDictionary.dictionaryWithDictionary_(
        {
            "name": AGGREGATE_NAME,  # kAudioAggregateDeviceNameKey
            "uid": f"notetaker-tap-{uuid.uuid4()}",  # kAudioAggregateDeviceUIDKey
            "private": True,  # kAudioAggregateDeviceIsPrivateKey: visible to this process only
            "tapautostart": True,  # kAudioAggregateDeviceTapAutoStartKey
            "taps": [{"uid": tap_uuid, "drift": True}],  # kAudioAggregateDeviceTapListKey
        }
    )
    aggregate_id = ctypes.c_uint32(0)
    status = b.lib.AudioHardwareCreateAggregateDevice(b.objc.pyobjc_id(aggregate), ctypes.byref(aggregate_id))
    if status != 0:
        b.lib.AudioHardwareDestroyProcessTap(tap_id.value)
        raise SystemAudioTapError(f"could not create the aggregate device for the system audio tap (status {status}).")

    return SystemAudioTap(
        tap_id=tap_id.value,
        aggregate_id=aggregate_id.value,
        device_name=AGGREGATE_NAME,
        tapped_bundle_id=tapped,
        fell_back_to_global=fell_back,
    )
