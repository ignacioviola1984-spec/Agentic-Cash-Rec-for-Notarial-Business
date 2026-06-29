"""Guarded LLM layer.

Hard rule enforced here: the LLM may only return category labels, expediente
assignment suggestions, and prose that restates already-computed facts. It never
produces monetary amounts. A grounding guard rejects any narrative containing a
number not present in the deterministic input. When no API key is configured the
whole layer degrades to deterministic rule-based heuristics, so the application
is fully functional offline.
"""
