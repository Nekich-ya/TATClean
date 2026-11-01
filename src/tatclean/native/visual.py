from __future__ import annotations

import ctypes
from ctypes import wintypes

from tatclean.core.tasks import TaskContext
from tatclean.native.winapi import bind, check_bool, library

VISUAL_FLAGS = {
    "client_animation": (0x1042, 0x1043),
    "drop_shadow": (0x1024, 0x1025),
    "menu_animation": (0x1002, 0x1003),
    "menu_fade": (0x1012, 0x1013),
    "tooltip_animation": (0x1016, 0x1017),
    "tooltip_fade": (0x1018, 0x1019),
    "combo_animation": (0x1004, 0x1005),
    "list_smooth_scroll": (0x1006, 0x1007),
    "selection_fade": (0x1014, 0x1015),
}


class AnimationInfo(ctypes.Structure):
    _fields_ = [("size", wintypes.UINT), ("enabled", ctypes.c_int)]


class VisualEffects:
    """Use public SPI settings instead of overwriting opaque UserPreferencesMask bits."""

    def __init__(self):
        self.spi = bind(library("user32"), "SystemParametersInfoW",
                        [wintypes.UINT, wintypes.UINT, ctypes.c_void_p, wintypes.UINT], wintypes.BOOL)

    def snapshot(self) -> dict[str, bool]:
        values = {}
        for name, (getter, _setter) in VISUAL_FLAGS.items():
            value = wintypes.BOOL()
            check_bool(self.spi(getter, 0, ctypes.byref(value), 0))
            values[name] = bool(value.value)
        animation = AnimationInfo(ctypes.sizeof(AnimationInfo), 0)
        check_bool(self.spi(0x48, animation.size, ctypes.byref(animation), 0))
        values["window_animation"] = bool(animation.enabled)
        return values

    def write(self, values: dict[str, bool], context: TaskContext) -> None:
        if set(values) != {*VISUAL_FLAGS, "window_animation"} or not all(isinstance(value, bool) for value in values.values()):
            raise ValueError("Incomplete or invalid visual-effects snapshot.")
        for name, (_getter, setter) in VISUAL_FLAGS.items():
            context.checkpoint()
            check_bool(self.spi(setter, 0, ctypes.c_void_p(int(values[name])), 3))
        context.checkpoint()
        animation = AnimationInfo(ctypes.sizeof(AnimationInfo), int(values["window_animation"]))
        check_bool(self.spi(0x49, animation.size, ctypes.byref(animation), 3))
