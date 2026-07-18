"""GPS plumbing (GPS.md): snapshot contract reader, PA1616S module config,
gpsd JSON client and the snapshot writer daemon.

Naming note: this repo-root package shadows gpsd's own `gps` python
bindings for anything running with the repo root on sys.path (run.py,
tests). That is deliberate and safe — nothing in this ecosystem imports
the bindings; every gpsd conversation here speaks the localhost JSON
socket directly (gps/gpsdio.py). Do not add `import gps`-bindings code
anywhere in the tree.
"""
