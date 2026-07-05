"""Screen registry. Index 0 is the launcher; the rest are candidate tiles in
display order. Screens with a `device_key` only show while that device is
plugged in (hotplug), the others are always present.
"""

from .home import LauncherScreen
from .lan import LanScreen
from .wifi import WifiScreen
from .sdr import SdrScreen
from .wifisniff import WifiSniffScreen
from .canbus import CanScreen
from .i2cscan import I2cScreen
from .serialmon import SerialScreen
from .kismet import KismetScreen
from .nodered import NodeRedScreen
from .aiscatcher import AisCatcherScreen
from .signalk import SignalKScreen
from .health import HealthScreen
from .settings import SettingsScreen

SCREENS = [
    LauncherScreen,      # must stay first
    LanScreen,
    WifiScreen,
    SdrScreen,           # device: sdr
    WifiSniffScreen,     # device: wifisniff (ALFA)
    CanScreen,           # device: can
    I2cScreen,           # device: i2c
    SerialScreen,        # device: serial
    KismetScreen,        # web app: kismet (tile shows if installed)
    NodeRedScreen,       # web app: node-red (tile shows if installed)
    AisCatcherScreen,    # web app: ais-catcher (needs RTL-SDR + installed)
    SignalKScreen,       # web app: signal-k (tile shows if signalk.service present)
    HealthScreen,
    SettingsScreen,
]
