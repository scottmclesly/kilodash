"""Scottina — the digital Swiss Army knife for hardware developers.

A fingertip touchscreen control panel for a Kali Raspberry Pi. Renders
directly to the ILI9486 SPI framebuffer (/dev/fb0) and reads the ADS7846
touch panel straight from evdev, so it needs no X server and is immune to
anything else holding the DRM device. Tap tiles to open tools; every screen
has a Back button.

(The package/paths keep the historical working name `kilodash`; the product
name is Scottina.)
"""

__version__ = "1.0.0"
