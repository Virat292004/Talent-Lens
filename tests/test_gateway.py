import importlib.util
from pathlib import Path
import sys
from types import SimpleNamespace
from jose import jwt

ROOT=Path(__file__).parents[1]
sys.path.insert(0,str(ROOT))
spec=importlib.util.spec_from_file_location("talentlens_gateway_test",ROOT/"api-gateway"/"main.py")
gateway=importlib.util.module_from_spec(spec)
sys.modules[spec.name]=gateway
spec.loader.exec_module(gateway)

def test_gateway_authenticates_signed_recruiter_token():
    token=jwt.encode({"sub":"recruiter","role":"recruiter"},gateway.SECRET,algorithm="HS256")
    request=SimpleNamespace(headers={"Authorization":f"Bearer {token}"})
    assert gateway.current_user(request)["sub"]=="recruiter"

def test_gateway_routes_cover_all_six_services():
    assert set(gateway.ROUTES)=={"jobs","resumes","candidates","parsing","matching","rankings"}
