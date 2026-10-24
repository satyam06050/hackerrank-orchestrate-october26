"""Import the canonical configuration from code/config.py without a code package."""
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

_path = Path(__file__).resolve().parent / "code" / "config.py"
_spec = spec_from_file_location("_ghost_audit_config", _path)
if _spec is None or _spec.loader is None:
    raise ImportError(f"Cannot load configuration from {_path}")
_module = module_from_spec(_spec)
_spec.loader.exec_module(_module)
for _name in dir(_module):
    if not _name.startswith("_"):
        globals()[_name] = getattr(_module, _name)
