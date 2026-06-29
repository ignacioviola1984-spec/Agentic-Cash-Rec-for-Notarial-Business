"""Service layer: orchestrates the deterministic core, persistence and audit.

Services never compute money themselves — they call the domain engine and only
persist its exact results. Every state change is recorded in the hash-chained
audit log.
"""
