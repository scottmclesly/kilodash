"""Screen registry. Order here is the left-to-right swipe order."""

from .home import HomeScreen
from .lan import LanScreen
from .wifi import WifiScreen
from .health import HealthScreen
from .settings import SettingsScreen

SCREENS = [
    HomeScreen,
    LanScreen,
    WifiScreen,
    HealthScreen,
    SettingsScreen,
]
