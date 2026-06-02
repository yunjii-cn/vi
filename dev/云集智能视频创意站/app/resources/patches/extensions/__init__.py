"""YunJi Extension Framework for LTX Desktop backend.

Architecture overview:
  patches/app_factory.py        → Thin orchestrator: create app + apply extensions
  patches/extensions/*.py       → Individual extension modules
  patches/extensions/_context.py → Shared context passed to all extensions
  patches/extensions/_utils.py  → Shared utilities (output path, ffmpeg, etc.)

When upstream (github.com/Lightricks/LTX-Desktop) updates:
  1. Update resources/backend/ with new upstream code
  2. Review each extension for compatibility
  3. Update UPSTREAM_VERSION in _context.py
  4. Test all extensions

Extension contract:
  Each extension module exports an ``install(app, ctx)`` function.
  - app: FastAPI application instance
  - ctx: ExtensionContext with shared state and utilities
"""
