"""兼容入口；新界面使用 :mod:`device_sidebar`。"""

from .device_sidebar import DeviceCard, DeviceSidebar

DeviceCardPanel = DeviceSidebar

__all__ = ["DeviceCard", "DeviceCardPanel", "DeviceSidebar"]
