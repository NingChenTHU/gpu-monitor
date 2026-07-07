from gpu_monitor.collectors.ascend import AscendNpuCollector
from gpu_monitor.collectors.base import DeviceCollector
from gpu_monitor.collectors.nvidia import NvidiaGpuCollector

DEFAULT_COLLECTORS: dict[str, DeviceCollector] = {
    "gpu": NvidiaGpuCollector(),
    "npu": AscendNpuCollector(),
}

__all__ = ["DEFAULT_COLLECTORS", "AscendNpuCollector", "DeviceCollector", "NvidiaGpuCollector"]
