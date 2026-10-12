"""
tests/run_stdlib.py — a tiny pytest-compatible runner using only the stdlib.

WHY THIS EXISTS: the real test runner is pytest (see README: `pytest -q`). But
in sandboxes without pytest installed, this script runs the SAME test files by
providing just enough of the pytest API the tests use: `pytest.raises`,
`pytest.mark.skipif`, `pytest.mark.<anything>`, and a `monkeypatch` fixture.

It is NOT a pytest replacement for real use — it's a verification aid so we can
prove the suite passes here. On your machine, `pytest -q` runs the identical
files. If the two ever disagree, pytest is the source of truth.
"""

from __future__ import annotations

import contextlib
import importlib
import os
import sys
import traceback
import types

# --- Build a fake `pytest` module good enough for our tests -------------------


class _Raises:
    def __init__(self, exc):
        self.exc = exc

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, tb):
        if exc_type is None:
            raise AssertionError(f"DID NOT RAISE {self.exc}")
        ok = issubclass(exc_type, self.exc)
        return ok  # swallow if it's the expected type


class _Mark:
    def __getattr__(self, _name):
        def deco(*dargs, **dkwargs):
            # skipif(condition, reason=...)
            cond = dargs[0] if dargs else dkwargs.get("condition", False)

            def wrap(fn):
                if _name == "skipif" and cond:
                    fn.__skip__ = dkwargs.get("reason", "skipif")
                return fn

            # support bare @pytest.mark.foo (no call) too
            if dargs and callable(dargs[0]) and not isinstance(dargs[0], bool):
                return dargs[0]
            return wrap

        return deco


class _MonkeyPatch:
    def __init__(self):
        self._undo = []

    def setenv(self, k, v):
        old = os.environ.get(k)
        self._undo.append(("env", k, old))
        os.environ[k] = v

    def delenv(self, k, raising=False):
        old = os.environ.get(k)
        self._undo.append(("env", k, old))
        os.environ.pop(k, None)

    def setattr(self, target, value, raising=True):
        # target like "app.llm.classify_with_llm"
        mod_path, attr = target.rsplit(".", 1)
        mod = importlib.import_module(mod_path)
        old = getattr(mod, attr, None)
        self._undo.append(("attr", mod, attr, old))
        setattr(mod, attr, value)

    def undo(self):
        for entry in reversed(self._undo):
            if entry[0] == "env":
                _, k, old = entry
                if old is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = old
            elif entry[0] == "attr":
                _, mod, attr, old = entry
                setattr(mod, attr, old)
        self._undo.clear()


fake_pytest = types.ModuleType("pytest")
fake_pytest.raises = lambda exc: _Raises(exc)
fake_pytest.mark = _Mark()
fake_pytest.skip = lambda reason="": (_ for _ in ()).throw(_Skip(reason))


class _Skip(Exception):
    pass


sys.modules["pytest"] = fake_pytest


# --- Discover and run test_*.py ----------------------------------------------
def main() -> int:
    here = os.path.dirname(os.path.abspath(__file__))
    root = os.path.dirname(here)
    sys.path.insert(0, root)

    test_files = sorted(
        f for f in os.listdir(here) if f.startswith("test_") and f.endswith(".py")
    )
    total = passed = failed = skipped = 0
    failures = []

    for tf in test_files:
        modname = f"tests.{tf[:-3]}"
        try:
            mod = importlib.import_module(modname)
        except Exception as e:
            print(f"  IMPORT ERROR {modname}: {e}")
            failed += 1
            failures.append((modname, traceback.format_exc()))
            continue

        # module-level skip marker (pytestmark)
        mod_skip = getattr(mod, "pytestmark", None)
        mod_skip_reason = getattr(mod_skip, "__skip_reason__", None) if mod_skip else None

        for name in sorted(dir(mod)):
            if not name.startswith("test_"):
                continue
            fn = getattr(mod, name)
            if not callable(fn):
                continue
            total += 1
            if getattr(fn, "__skip__", None):
                skipped += 1
                continue

            mp = _MonkeyPatch()
            try:
                # inject monkeypatch if the test wants it
                import inspect

                kwargs = {}
                if "monkeypatch" in inspect.signature(fn).parameters:
                    kwargs["monkeypatch"] = mp
                fn(**kwargs)
                passed += 1
            except _Skip as s:
                skipped += 1
            except Exception:
                failed += 1
                failures.append((f"{modname}::{name}", traceback.format_exc()))
                print(f"  FAIL {modname}::{name}")
            finally:
                mp.undo()

    print()
    print(f"  {passed} passed, {failed} failed, {skipped} skipped, {total} total")
    if failures:
        print("\n==== FAILURES ====")
        for name, tb in failures:
            print(f"\n--- {name} ---")
            print(tb)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
