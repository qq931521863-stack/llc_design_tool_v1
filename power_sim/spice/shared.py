"""Typed wrapper around ngspice's shared-library API.

ngspice's shared-library implementation is process-global, not an ordinary
re-entrant object API.  In particular, repeatedly calling ``ngSpice_Init`` from
multiple Python wrapper instances can replace callback pointers while ngspice
still owns global state and may lead to a native crash.  This module therefore
exposes one persistent Python wrapper per process and only updates the mutable
Python-side handlers between simulation runs.

Controller mathematics remain in ``power_sim.digital_control``; this module owns
only simulator lifetime, callbacks, circuits, vectors and synchronization.
"""
from __future__ import annotations

import ctypes as ct
from ctypes.util import find_library
from pathlib import Path
import os
import sys
import threading
import time
from typing import Callable, Sequence

import numpy as np


class NgComplex(ct.Structure):
    _fields_ = [("cx_real", ct.c_double), ("cx_imag", ct.c_double)]


class VectorInfo(ct.Structure):
    _fields_ = [
        ("v_name", ct.c_char_p),
        ("v_type", ct.c_int),
        ("v_flags", ct.c_short),
        ("v_realdata", ct.POINTER(ct.c_double)),
        ("v_compdata", ct.POINTER(NgComplex)),
        ("v_length", ct.c_int),
    ]


class VecValues(ct.Structure):
    _fields_ = [
        ("name", ct.c_char_p),
        ("creal", ct.c_double),
        ("cimag", ct.c_double),
        ("is_scale", ct.c_bool),
        ("is_complex", ct.c_bool),
    ]


class VecValuesAll(ct.Structure):
    _fields_ = [
        ("veccount", ct.c_int),
        ("vecindex", ct.c_int),
        ("vecsa", ct.POINTER(ct.POINTER(VecValues))),
    ]


class VecInfo(ct.Structure):
    _fields_ = [
        ("number", ct.c_int),
        ("vecname", ct.c_char_p),
        ("is_real", ct.c_bool),
        ("pdvec", ct.c_void_p),
        ("pdvecscale", ct.c_void_p),
    ]


class VecInfoAll(ct.Structure):
    _fields_ = [
        ("name", ct.c_char_p),
        ("title", ct.c_char_p),
        ("date", ct.c_char_p),
        ("type", ct.c_char_p),
        ("veccount", ct.c_int),
        ("vecs", ct.POINTER(ct.POINTER(VecInfo))),
    ]


SendCharCB = ct.CFUNCTYPE(ct.c_int, ct.c_char_p, ct.c_int, ct.c_void_p)
SendStatCB = ct.CFUNCTYPE(ct.c_int, ct.c_char_p, ct.c_int, ct.c_void_p)
ControlledExitCB = ct.CFUNCTYPE(ct.c_int, ct.c_int, ct.c_bool, ct.c_bool, ct.c_int, ct.c_void_p)
SendDataCB = ct.CFUNCTYPE(ct.c_int, ct.POINTER(VecValuesAll), ct.c_int, ct.c_int, ct.c_void_p)
SendInitDataCB = ct.CFUNCTYPE(ct.c_int, ct.POINTER(VecInfoAll), ct.c_int, ct.c_void_p)
BGThreadRunningCB = ct.CFUNCTYPE(ct.c_int, ct.c_bool, ct.c_int, ct.c_void_p)
GetVSRCDataCB = ct.CFUNCTYPE(ct.c_int, ct.POINTER(ct.c_double), ct.c_double, ct.c_char_p, ct.c_int, ct.c_void_p)
GetISRCDataCB = ct.CFUNCTYPE(ct.c_int, ct.POINTER(ct.c_double), ct.c_double, ct.c_char_p, ct.c_int, ct.c_void_p)
GetSyncDataCB = ct.CFUNCTYPE(
    ct.c_int,
    ct.c_double,
    ct.POINTER(ct.c_double),
    ct.c_double,
    ct.c_int,
    ct.c_int,
    ct.c_int,
    ct.c_void_p,
)

ExternalSourceCallback = Callable[[float, str], float]
SyncCallback = Callable[[float, float, float, int, int], float | None]
DataCallback = Callable[[int, dict[str, complex]], None]


def _library_candidates() -> list[str]:
    candidates: list[str] = []
    env = os.environ.get("NGSPICE_SHARED_LIB")
    if env:
        candidates.append(env)
    found = find_library("ngspice")
    if found:
        candidates.append(found)
    if sys.platform.startswith("win"):
        candidates += [
            r"C:\Program Files\ngspice\bin\ngspice.dll",
            r"C:\Program Files\ngspice\bin\libngspice-0.dll",
            r"C:\Program Files\ngspice\ngspice.dll",
        ]
    elif sys.platform == "darwin":
        candidates += [
            "/opt/homebrew/lib/libngspice.dylib",
            "/usr/local/lib/libngspice.dylib",
        ]
    else:
        candidates += [
            "/usr/lib/x86_64-linux-gnu/libngspice.so.0",
            "/usr/lib/x86_64-linux-gnu/libngspice.so",
            "/usr/local/lib/libngspice.so",
        ]
    return list(dict.fromkeys(candidates))


def find_ngspice_shared_library(explicit: str | Path | None = None) -> str | None:
    candidates = [str(Path(explicit).expanduser())] if explicit is not None else _library_candidates()
    for candidate in candidates:
        try:
            ct.CDLL(candidate)
            return candidate
        except OSError:
            continue
    return None


class NgSpiceSharedLibrary:
    """Process-singleton shared-ngspice session.

    ngspice's C shared library owns global simulator/callback state.  Returning
    the same Python object for repeated construction prevents repeated native
    ``ngSpice_Init`` calls and makes repeated GUI simulations safe by design.
    Python callbacks are mutable and can be replaced for each run.
    """

    _instance: "NgSpiceSharedLibrary | None" = None
    _instance_lock = threading.Lock()

    def __new__(cls, library: str | Path | None = None):
        del library
        with cls._instance_lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
            return cls._instance

    def __init__(self, library: str | Path | None = None):
        if getattr(self, "_constructed", False):
            return
        resolved = find_ngspice_shared_library(library)
        if resolved is None:
            # Do not leave a half-constructed singleton behind after failure.
            type(self)._instance = None
            raise FileNotFoundError(
                "shared ngspice library not found; install libngspice and/or set NGSPICE_SHARED_LIB"
            )
        self._constructed = True
        self.library_path = resolved
        self.lib = ct.CDLL(resolved)
        self.messages: list[str] = []
        self.status_messages: list[str] = []
        self.exit_status: int | None = None
        self.background_running = False
        self.data_callback_count = 0
        self.init_callback_count = 0
        self.init_vector_names: tuple[str, ...] = ()
        self.last_data_names: tuple[str, ...] = ()
        self._data_callback: DataCallback | None = None
        self._vsrc_callback: ExternalSourceCallback | None = None
        self._isrc_callback: ExternalSourceCallback | None = None
        self._sync_callback: SyncCallback | None = None
        self._bg_started = threading.Event()
        self._bg_finished = threading.Event()
        self._callbacks: list[object] = []
        self._initialized = False
        self._sync_initialized = False
        self._configure_symbols()

    @staticmethod
    def available() -> bool:
        return find_ngspice_shared_library() is not None

    def _configure_symbols(self) -> None:
        self.lib.ngSpice_Init.restype = ct.c_int
        self.lib.ngSpice_Init.argtypes = [
            SendCharCB,
            SendStatCB,
            ControlledExitCB,
            SendDataCB,
            SendInitDataCB,
            BGThreadRunningCB,
            ct.c_void_p,
        ]
        self.lib.ngSpice_Command.restype = ct.c_int
        self.lib.ngSpice_Command.argtypes = [ct.c_char_p]
        self.lib.ngSpice_Circ.restype = ct.c_int
        self.lib.ngSpice_Circ.argtypes = [ct.POINTER(ct.c_char_p)]
        self.lib.ngGet_Vec_Info.restype = ct.POINTER(VectorInfo)
        self.lib.ngGet_Vec_Info.argtypes = [ct.c_char_p]
        self.lib.ngSpice_CurPlot.restype = ct.c_char_p
        self.lib.ngSpice_CurPlot.argtypes = []
        self.lib.ngSpice_AllPlots.restype = ct.POINTER(ct.c_char_p)
        self.lib.ngSpice_AllPlots.argtypes = []
        self.lib.ngSpice_AllVecs.restype = ct.POINTER(ct.c_char_p)
        self.lib.ngSpice_AllVecs.argtypes = [ct.c_char_p]
        self.lib.ngSpice_running.restype = ct.c_bool
        self.lib.ngSpice_running.argtypes = []
        self.lib.ngSpice_SetBkpt.restype = ct.c_bool
        self.lib.ngSpice_SetBkpt.argtypes = [ct.c_double]
        if hasattr(self.lib, "ngSpice_Init_Sync"):
            self.lib.ngSpice_Init_Sync.restype = ct.c_int
            self.lib.ngSpice_Init_Sync.argtypes = [
                GetVSRCDataCB,
                GetISRCDataCB,
                GetSyncDataCB,
                ct.POINTER(ct.c_int),
                ct.c_void_p,
            ]

    @staticmethod
    def _decode(value: bytes | None) -> str:
        return value.decode("utf-8", errors="replace") if value else ""

    @staticmethod
    def _decode_null_terminated(array: ct.POINTER(ct.c_char_p) | None) -> tuple[str, ...]:
        if not array:
            return ()
        values: list[str] = []
        i = 0
        while True:
            item = array[i]
            if not item:
                break
            values.append(item.decode("utf-8", errors="replace"))
            i += 1
            if i > 100_000:
                raise RuntimeError("ngspice returned an unterminated string array")
        return tuple(values)

    def _reset_run_diagnostics(self) -> None:
        self.messages.clear()
        self.status_messages.clear()
        self.exit_status = None
        self.background_running = False
        self.data_callback_count = 0
        self.init_callback_count = 0
        self.init_vector_names = ()
        self.last_data_names = ()
        self._bg_started.clear()
        self._bg_finished.clear()

    def _install_main_callbacks(self) -> None:
        @SendCharCB
        def send_char(text, ident, userdata):
            del ident, userdata
            self.messages.append(self._decode(text))
            return 0

        @SendStatCB
        def send_stat(text, ident, userdata):
            del ident, userdata
            self.status_messages.append(self._decode(text))
            return 0

        @ControlledExitCB
        def controlled_exit(status, immediate, exit_on_quit, ident, userdata):
            del immediate, exit_on_quit, ident, userdata
            self.exit_status = int(status)
            return 0

        @SendDataCB
        def send_data(values_ptr, count, ident, userdata):
            del count, ident, userdata
            if not values_ptr:
                return 0
            values = values_ptr.contents
            point: dict[str, complex] = {}
            for i in range(values.veccount):
                item_ptr = values.vecsa[i]
                if not item_ptr:
                    continue
                item = item_ptr.contents
                point[self._decode(item.name)] = complex(
                    item.creal,
                    item.cimag if item.is_complex else 0.0,
                )
            self.data_callback_count += 1
            self.last_data_names = tuple(point.keys())
            if self._data_callback is not None:
                self._data_callback(int(values.vecindex), point)
            return 0

        @SendInitDataCB
        def send_init_data(info_ptr, ident, userdata):
            del ident, userdata
            self.init_callback_count += 1
            if not info_ptr:
                self.init_vector_names = ()
                return 0
            info = info_ptr.contents
            names: list[str] = []
            for i in range(int(info.veccount)):
                item_ptr = info.vecs[i]
                if item_ptr:
                    names.append(self._decode(item_ptr.contents.vecname))
            self.init_vector_names = tuple(names)
            return 0

        @BGThreadRunningCB
        def bg_running(exited, ident, userdata):
            del ident, userdata
            # ngspice passes its internal fl_exited value: FALSE when the
            # background worker starts and TRUE when it exits.
            if bool(exited):
                self.background_running = False
                if self._bg_started.is_set():
                    self._bg_finished.set()
            else:
                self.background_running = True
                self._bg_started.set()
                self._bg_finished.clear()
            return 0

        self._callbacks = [
            send_char,
            send_stat,
            controlled_exit,
            send_data,
            send_init_data,
            bg_running,
        ]
        rc = int(self.lib.ngSpice_Init(*self._callbacks, None))
        if rc != 0:
            raise RuntimeError(f"ngSpice_Init failed with status {rc}")
        self._initialized = True

    def _install_sync_callbacks(self) -> None:
        if self._sync_initialized:
            return
        if not hasattr(self.lib, "ngSpice_Init_Sync"):
            raise RuntimeError("loaded ngspice shared library does not export ngSpice_Init_Sync")

        @GetVSRCDataCB
        def get_vsrc(value_ptr, time_s, name, ident, userdata):
            del ident, userdata
            if value_ptr:
                value_ptr[0] = 0.0 if self._vsrc_callback is None else float(
                    self._vsrc_callback(float(time_s), self._decode(name))
                )
            return 0

        @GetISRCDataCB
        def get_isrc(value_ptr, time_s, name, ident, userdata):
            del ident, userdata
            if value_ptr:
                value_ptr[0] = 0.0 if self._isrc_callback is None else float(
                    self._isrc_callback(float(time_s), self._decode(name))
                )
            return 0

        @GetSyncDataCB
        def get_sync(time_s, delta_ptr, old_delta, redostep, ident, location, userdata):
            del ident, userdata
            if delta_ptr and self._sync_callback is not None:
                requested = self._sync_callback(
                    float(time_s),
                    float(delta_ptr[0]),
                    float(old_delta),
                    int(redostep),
                    int(location),
                )
                if requested is not None and requested > 0.0:
                    delta_ptr[0] = float(requested)
            return 0

        self._callbacks.extend([get_vsrc, get_isrc, get_sync])
        ident = ct.c_int(0)
        rc = int(
            self.lib.ngSpice_Init_Sync(
                get_vsrc,
                get_isrc,
                get_sync,
                ct.byref(ident),
                None,
            )
        )
        if rc != 0:
            raise RuntimeError(f"ngSpice_Init_Sync failed with status {rc}")
        self._sync_initialized = True

    def initialize(
        self,
        *,
        data_callback: DataCallback | None = None,
        external_voltage: ExternalSourceCallback | None = None,
        external_current: ExternalSourceCallback | None = None,
        sync_callback: SyncCallback | None = None,
    ) -> None:
        """Initialize native ngspice once, then retarget Python handlers per run."""
        if self.is_running() if self._initialized else False:
            raise RuntimeError("cannot reconfigure shared ngspice callbacks while a simulation is running")

        self._reset_run_diagnostics()
        self._data_callback = data_callback
        self._vsrc_callback = external_voltage
        self._isrc_callback = external_current
        self._sync_callback = sync_callback

        if not self._initialized:
            self._install_main_callbacks()
        if any(callback is not None for callback in (external_voltage, external_current, sync_callback)):
            self._install_sync_callbacks()

    def command(self, text: str) -> int:
        if not self._initialized:
            raise RuntimeError("shared ngspice session is not initialized")
        return int(self.lib.ngSpice_Command(str(text).encode("utf-8")))

    def load_circuit(self, lines: Sequence[str]) -> int:
        if not self._initialized:
            raise RuntimeError("shared ngspice session is not initialized")
        if self.is_running():
            raise RuntimeError("cannot load a new circuit while shared ngspice is running")
        encoded = [str(line).encode("utf-8") for line in lines]
        array_type = ct.c_char_p * (len(encoded) + 1)
        array = array_type(*encoded, None)
        return int(self.lib.ngSpice_Circ(array))

    def current_plot(self) -> str:
        return self._decode(self.lib.ngSpice_CurPlot())

    def all_plots(self) -> tuple[str, ...]:
        return self._decode_null_terminated(self.lib.ngSpice_AllPlots())

    def all_vectors(self, plot_name: str | None = None) -> tuple[str, ...]:
        plot = plot_name or self.current_plot()
        if not plot:
            return ()
        return self._decode_null_terminated(self.lib.ngSpice_AllVecs(plot.encode("utf-8")))

    def set_breakpoint(self, time_s: float) -> bool:
        # This is a solver timestep breakpoint, not a simulation pause.
        return bool(self.lib.ngSpice_SetBkpt(float(time_s)))

    def is_running(self) -> bool:
        return bool(self.lib.ngSpice_running())

    def run_background(self, *, timeout_s: float = 30.0, poll_s: float = 0.001) -> None:
        """Start ``bg_run`` and wait for confirmed worker start and completion."""
        timeout = float(timeout_s)
        poll = float(poll_s)
        if timeout <= 0.0 or poll <= 0.0:
            raise ValueError("timeout_s and poll_s must be positive")
        self._bg_started.clear()
        self._bg_finished.clear()
        self.background_running = False
        rc = self.command("bg_run")
        if rc != 0:
            raise RuntimeError(f"shared ngspice bg_run failed to start with status {rc}")

        deadline = time.monotonic() + timeout
        while not self._bg_started.is_set():
            if self.is_running():
                self._bg_started.set()
                break
            if time.monotonic() >= deadline:
                raise TimeoutError("shared ngspice background thread never entered running state")
            time.sleep(poll)

        while True:
            if self._bg_finished.is_set() or not self.is_running():
                break
            if time.monotonic() >= deadline:
                raise TimeoutError(f"shared ngspice did not finish within {timeout:.3g} s")
            time.sleep(poll)
        self._bg_finished.wait(timeout=min(0.1, max(poll, 1e-4)))
        self.background_running = False

    def wait_until_idle(self, *, timeout_s: float = 30.0, poll_s: float = 0.001) -> None:
        timeout = float(timeout_s)
        poll = float(poll_s)
        if timeout <= 0.0 or poll <= 0.0:
            raise ValueError("timeout_s and poll_s must be positive")
        deadline = time.monotonic() + timeout
        while self.is_running():
            if time.monotonic() >= deadline:
                raise TimeoutError(f"shared ngspice did not finish within {timeout:.3g} s")
            time.sleep(poll)

    def vector(self, name: str) -> np.ndarray:
        info_ptr = self.lib.ngGet_Vec_Info(str(name).encode("utf-8"))
        if not info_ptr:
            raise KeyError(f"ngspice vector not found: {name}")
        info = info_ptr.contents
        length = int(info.v_length)
        if length <= 0:
            return np.asarray([], dtype=float)
        if info.v_realdata:
            return np.ctypeslib.as_array(info.v_realdata, shape=(length,)).astype(float, copy=True)
        if info.v_compdata:
            raw = np.ctypeslib.as_array(info.v_compdata, shape=(length,))
            return np.asarray(
                [complex(item.cx_real, item.cx_imag) for item in raw],
                dtype=complex,
            )
        return np.asarray([], dtype=float)


__all__ = [
    "NgComplex",
    "VectorInfo",
    "VecValues",
    "VecValuesAll",
    "VecInfo",
    "VecInfoAll",
    "NgSpiceSharedLibrary",
    "find_ngspice_shared_library",
]
