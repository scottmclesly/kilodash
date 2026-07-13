"""Decode-table store — shared contract code (see TABLES.md at the repo root).

This package holds the *code* half of the table store: the schema validator
(`tables.validate`) and the store/manifest helpers (`tables.store`). The
sibling directories (`pgn/`, `dbc/`, `uploads/`) hold the *data*. Both the
converter service and the NMEA2K screen import from here, so the two ends of
the contract can never drift apart.
"""
