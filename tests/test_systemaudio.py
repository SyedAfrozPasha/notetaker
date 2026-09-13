import ctypes
from unittest.mock import MagicMock

import pytest

from notetaker import systemaudio
from notetaker.systemaudio import (
    AGGREGATE_NAME,
    SystemAudioTapError,
    create_system_audio_tap,
    find_process_objects,
    tap_support_problem,
)


class FakeTapDescription:
    """Records how CATapDescription was built."""

    instances = []

    def __init__(self, kind, processes):
        self.kind = kind
        self.processes = processes
        self.name = None
        self.mute = None
        FakeTapDescription.instances.append(self)

    def setName_(self, name):
        self.name = name

    def setMuteBehavior_(self, value):
        self.mute = value

    def UUID(self):
        return MagicMock(UUIDString=lambda: "TAP-UUID-1234")


class FakeTapDescriptionClass:
    @staticmethod
    def alloc():
        class _Alloc:
            def initStereoGlobalTapButExcludeProcesses_(self, excluded):
                return FakeTapDescription("global", list(excluded))

            def initStereoMixdownOfProcesses_(self, processes):
                return FakeTapDescription("processes", list(processes))

        return _Alloc()


def _fake_bindings(monkeypatch, *, tap_status=0, aggregate_status=0, process_bundles=None):
    """Installs fake CoreAudio bindings. `process_bundles` maps audio object
    id -> bundle id for the process-list enumeration."""
    FakeTapDescription.instances.clear()
    process_bundles = process_bundles or {}
    lib = MagicMock()
    calls = []

    def create_tap(desc_ptr, out):
        calls.append("create_tap")
        out._obj.value = 77
        return tap_status

    def create_agg(dict_ptr, out):
        calls.append("create_aggregate")
        out._obj.value = 88
        return aggregate_status

    lib.AudioHardwareCreateProcessTap.side_effect = create_tap
    lib.AudioHardwareCreateAggregateDevice.side_effect = create_agg
    lib.AudioHardwareDestroyAggregateDevice.side_effect = lambda i: calls.append(("destroy_aggregate", i)) or 0
    lib.AudioHardwareDestroyProcessTap.side_effect = lambda i: calls.append(("destroy_tap", i)) or 0

    ids = list(process_bundles)

    def get_size(obj, addr, qsize, qual, size_out):
        size_out._obj.value = 4 * len(ids)
        return 0

    def get_data(obj, addr, qsize, qual, size_out, data):
        selector = ctypes.cast(addr, ctypes.POINTER(systemaudio._PropertyAddress)).contents.mSelector
        if selector == systemaudio._fourcc("prs#"):
            for i, value in enumerate(ids):
                data[i] = value
            size_out._obj.value = 4 * len(ids)
            return 0
        if selector == systemaudio._fourcc("pbid"):
            # hand back a fake CFString pointer; objc_object below turns it into the bundle id
            ctypes.cast(data, ctypes.POINTER(ctypes.c_void_p)).contents.value = 1000 + obj
            return 0
        return -1

    lib.AudioObjectGetPropertyDataSize.side_effect = get_size
    lib.AudioObjectGetPropertyData.side_effect = get_data

    objc = MagicMock()
    objc.pyobjc_id = lambda o: 1
    objc.objc_object = lambda c_void_p: process_bundles[c_void_p - 1000]

    bindings = MagicMock(lib=lib, objc=objc, CATapDescription=FakeTapDescriptionClass)
    bindings.NSArray.array = lambda: []
    bindings.NSArray.arrayWithArray_ = lambda items: list(items)
    captured = {}
    bindings.NSDictionary.dictionaryWithDictionary_ = lambda d: captured.setdefault("aggregate", d)
    monkeypatch.setattr(systemaudio, "_bindings", lambda: bindings)
    return calls, captured


def test_global_tap_builds_unbound_private_aggregate(monkeypatch):
    calls, captured = _fake_bindings(monkeypatch)

    tap = create_system_audio_tap()

    assert (tap.tap_id, tap.aggregate_id, tap.device_name) == (77, 88, AGGREGATE_NAME)
    assert tap.tapped_bundle_id is None and tap.fell_back_to_global is False
    desc = FakeTapDescription.instances[0]
    assert desc.kind == "global" and desc.mute == 0 and desc.name
    agg = captured["aggregate"]
    assert agg["private"] is True and agg["tapautostart"] is True
    assert agg["taps"] == [{"uid": "TAP-UUID-1234", "drift": True}]
    assert "subdevices" not in agg and "master" not in agg  # not bound to any output device
    assert calls == ["create_tap", "create_aggregate"]


def test_process_tap_targets_only_the_named_app(monkeypatch):
    _fake_bindings(monkeypatch, process_bundles={5: "com.apple.Music", 9: "com.microsoft.teams2"})

    assert find_process_objects("com.microsoft.teams2") == [9]
    tap = create_system_audio_tap("com.microsoft.teams2")

    assert tap.tapped_bundle_id == "com.microsoft.teams2"
    assert FakeTapDescription.instances[0].kind == "processes"
    assert FakeTapDescription.instances[0].processes == [9]


def test_process_tap_falls_back_to_global_when_app_not_running(monkeypatch):
    _fake_bindings(monkeypatch, process_bundles={5: "com.apple.Music"})

    tap = create_system_audio_tap("com.microsoft.teams2")

    assert tap.fell_back_to_global is True and tap.tapped_bundle_id is None
    assert FakeTapDescription.instances[0].kind == "global"


def test_close_destroys_aggregate_then_tap_once(monkeypatch):
    calls, _ = _fake_bindings(monkeypatch)
    tap = create_system_audio_tap()

    tap.close()
    tap.close()

    assert calls[2:] == [("destroy_aggregate", 88), ("destroy_tap", 77)]


def test_context_manager_closes(monkeypatch):
    calls, _ = _fake_bindings(monkeypatch)
    with create_system_audio_tap():
        pass
    assert ("destroy_tap", 77) in calls


def test_tap_creation_failure_is_user_facing(monkeypatch):
    _fake_bindings(monkeypatch, tap_status=-1)
    with pytest.raises(SystemAudioTapError, match="System Audio Recording"):
        create_system_audio_tap()


def test_aggregate_failure_destroys_the_tap(monkeypatch):
    calls, _ = _fake_bindings(monkeypatch, aggregate_status=-2)
    with pytest.raises(SystemAudioTapError, match="aggregate"):
        create_system_audio_tap()
    assert ("destroy_tap", 77) in calls


def test_tap_support_problem_reports_old_macos(monkeypatch):
    monkeypatch.setattr(systemaudio.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(systemaudio.platform, "mac_ver", lambda: ("13.6.1", ("", "", ""), ""))
    assert "14.2+" in tap_support_problem()


def test_tap_support_problem_reports_non_mac(monkeypatch):
    monkeypatch.setattr(systemaudio.platform, "system", lambda: "Linux")
    assert "macOS" in tap_support_problem()


def test_tap_support_problem_reports_missing_bindings(monkeypatch):
    monkeypatch.setattr(systemaudio.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(systemaudio.platform, "mac_ver", lambda: ("26.0", ("", "", ""), ""))
    monkeypatch.setattr(systemaudio, "_bindings", lambda: (_ for _ in ()).throw(OSError("no CoreAudio")))
    assert "no CoreAudio" in tap_support_problem()


def test_tap_support_problem_none_when_bindings_load(monkeypatch):
    monkeypatch.setattr(systemaudio.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(systemaudio.platform, "mac_ver", lambda: ("26.6.2", ("", "", ""), ""))
    monkeypatch.setattr(systemaudio, "_bindings", lambda: object())
    assert tap_support_problem() is None
