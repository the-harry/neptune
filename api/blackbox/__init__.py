"""Two-sided blackbox flight recorder (spec + logging addendum).

Lazy exports so the stdlib-only analysis CLI (`python -m blackbox.rovlog`) never
drags in FastAPI, while the app still gets `BlackBox` / `build_router` on demand.
"""

__all__ = ["BlackBox", "build_router"]


def __getattr__(name):
    if name == "BlackBox":
        from .recorder import BlackBox
        return BlackBox
    if name == "build_router":
        from .service import build_router
        return build_router
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
