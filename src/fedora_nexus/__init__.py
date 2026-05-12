"""fedora-nexus — dependency graph engine with MCP interface."""

from importlib.metadata import PackageNotFoundError, version as _version

try:
    __version__: str = _version("fedora-nexus")
except PackageNotFoundError:
    __version__ = "0.0.0+dev"

