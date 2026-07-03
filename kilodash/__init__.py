"""kilodash - a fingertip touchscreen control panel for a Kali Raspberry Pi.

Renders directly to the ILI9486 SPI framebuffer (/dev/fb0) and reads the
ADS7846 touch panel straight from evdev, so it needs no X server and is
immune to anything else holding the DRM device. Swipe left/right to move
between screens; tap to act.
"""

__version__ = "1.0.0"
