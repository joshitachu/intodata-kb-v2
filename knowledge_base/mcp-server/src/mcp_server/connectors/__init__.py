"""Governed, provider-neutral runtime evidence connectors.

The package deliberately keeps vendor integrations outside generation prompts and
pipeline code.  Providers receive typed semantic requests through the catalogue;
they never receive arbitrary query DSL from callers.
"""

from .catalog import connector_catalog

__all__ = ["connector_catalog"]
