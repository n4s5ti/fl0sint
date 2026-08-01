"""Deployment-owned projection registry.

Profiles are intentionally supplied by reviewed system configuration.  The stock
registry is empty, so template declarations fail closed until deployment adds an
approved profile implementation.
"""
from .contracts import ApprovedProjectionRegistry


projection_registry = ApprovedProjectionRegistry()
