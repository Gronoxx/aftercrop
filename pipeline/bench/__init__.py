# Written with an SI tool (Claude, Anthropic) for NASA Space Apps 2026. See AI_USE.md.
"""Test bench (M4): statistics and rules that decide what the page may claim.

Pure logic. The modules receive simulated and observed values as arguments and
never run the engine. Every threshold comes from the ``protocol`` mapping
(``config/region_norte_pr.yaml``, block ``protocol:``), never from these files.
"""
