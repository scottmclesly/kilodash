"""Screen registry. Index 0 is the launcher (home); the rest become tiles on
it, in this order.
"""

from .home import LauncherScreen
from .lan import LanScreen
from .wifi import WifiScreen
from .health import HealthScreen
from .settings import SettingsScreen

SCREENS = [
    LauncherScreen,   # must stay first
    LanScreen,
    WifiScreen,
    HealthScreen,
    SettingsScreen,
]
