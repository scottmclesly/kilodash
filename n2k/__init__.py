"""NMEA2000 *node* code — the TX side of the house.

Not to be confused with `kilodash/n2k.py` (the RX-only semantic-decode
core for the NMEA2K screen). This repo-root package exists precisely so
the transmit carve-out has a crisp boundary the AST scan can point at
(tests/test_txscan.py): `n2k/node.py` is the ONE module in the tree
allowed to transmit on a CAN socket — the GNSS source node's address
claim, claim defense, ISO request responses and the five GNSS PGNs,
started and stopped only by an explicit user action on the NMEA2K tile.
`n2k/fastpacket_tx.py` builds frames but owns no socket.

Everything under kilodash/ remains diagnostics-only, RX-only.
"""
