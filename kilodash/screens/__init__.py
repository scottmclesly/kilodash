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
from .n2k import N2kScreen
from .tables import TablesScreen
from .i2cscan import I2cScreen
from .serialmon import SerialScreen
from .logic import LogicScreen
from .files import FilesScreen
from .lightdock import LightDockScreen
from .kismet import KismetScreen
from .nodered import NodeRedScreen
from .aiscatcher import AisCatcherScreen
from .signalk import SignalKScreen
from .pomodoro import PomodoroScreen
from .health import HealthScreen
from .settings import SettingsScreen

SCREENS = [
    LauncherScreen,      # must stay first
    LanScreen,
    WifiScreen,
    SdrScreen,           # device: sdr
    WifiSniffScreen,     # device: wifisniff (ALFA)
    CanScreen,           # device: can (raw-bus forensics)
    N2kScreen,           # device: can (semantic decode from PGN tables)
    I2cScreen,           # device: i2c
    SerialScreen,        # device: serial
    LogicScreen,         # device: la (FX2LP logic analyzer)
    FilesScreen,         # device: usbstick (log offload + decode tables)
    LightDockScreen,     # device: scottinalight (dock auto-sync)
    TablesScreen,        # always visible: converter service + store mirror
    KismetScreen,        # web app: kismet (tile shows if installed)
    NodeRedScreen,       # web app: node-red (tile shows if installed)
    AisCatcherScreen,    # web app: ais-catcher (needs RTL-SDR + installed)
    SignalKScreen,       # web app: signal-k (tile shows if signalk.service present)
    PomodoroScreen,      # focus timer; keeps running in the background
    HealthScreen,
    SettingsScreen,
]
