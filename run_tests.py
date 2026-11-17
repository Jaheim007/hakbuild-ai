"""Runner de tests stdlib : aucun pytest requis.

Usage: python run_tests.py
"""
from __future__ import annotations

import importlib.util
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))


def load(path: Path):
    spec = importlib.util.spec_from_file_location(path.stem, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main() -> int:
    files = sorted((ROOT / "tests").glob("test_*.py"))
    if not files:
        print("Aucun fichier de test.")
        return 1

    total = passed = failed = 0
    for path in files:
        module = load(path)
        classes = [
            obj for name, obj in vars(module).items()
            if isinstance(obj, type) and name.startswith("Test")
        ]
        for cls in classes:
            for name in sorted(n for n in dir(cls) if n.startswith("test_")):
                total += 1
                instance = cls(name)
                try:
                    instance.setUp()
                    getattr(instance, name)()
                except Exception as exc:  # noqa: BLE001
                    failed += 1
                    print(f"[FAIL] {cls.__name__}.{name}: {exc}")
                    traceback.print_exc()
                else:
                    passed += 1
                    print(f"[OK]   {cls.__name__}.{name}")
                finally:
                    try:
                        instance.tearDown()
                    except Exception:  # noqa: BLE001
                        pass

    print()
    print("=== HAKBUILD MVP BUILD CHECK ===")
    print(f"=== SUMMARY === {passed}/{total} ok, {failed} echec(s)")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
