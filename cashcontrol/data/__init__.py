"""Persistence layer: SQLite schema, repositories and the hash-chained audit log.

Money is stored as integer centavos (``*_centavos`` columns) so SQL aggregates
stay exact. Conversion to/from :class:`decimal.Decimal` happens only at this
boundary.
"""
