"""Import all FastAPI app modules without starting infrastructure connections."""
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
files=[ROOT/"api-gateway"/"main.py",*sorted((ROOT/"services").glob("*/main.py"))]
for path in files:
    name="talentlens_"+"_".join(path.relative_to(ROOT).with_suffix("").parts).replace("-","_")
    spec=spec_from_file_location(name,path)
    module=module_from_spec(spec)
    sys.modules[name]=module
    spec.loader.exec_module(module)
print(f"Imported {len(files)} FastAPI application modules")
