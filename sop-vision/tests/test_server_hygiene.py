from pathlib import Path


def test_no_class_definitions_inside_create_app():
    src = Path(__file__).resolve().parents[1].joinpath("visual", "server.py").read_text(encoding="utf-8")
    start = src.find("def create_app(")
    end = src.find("def build_app(")
    assert 0 < start < end, "create_app/build_app structure changed"
    bad = [ln for ln in src[start:end].splitlines() if ln.startswith("    class ") and "BaseModel" in ln]
    assert not bad, "nested pydantic body model in create_app breaks FastAPI annotation parsing: " + " | ".join(bad)


def test_all_body_models_are_module_level():
    src = Path(__file__).resolve().parents[1].joinpath("visual", "server.py").read_text(encoding="utf-8")
    models = ["LoginBody", "UserBody", "AlertPatchBody", "CompileBody", "StepsBody", "SignalBody",
              "ZonesBody", "SourceBody", "IntervalsBody", "PlanPointBody", "TruthBody", "GeoBody", "OnvifDiscoverBody", "OnvifAdoptBody"]
    for m in models:
        assert f"class {m}(BaseModel)" in src, f"{m} missing"
        indented = [ln for ln in src.splitlines() if ln.startswith("    ") and f"class {m}(BaseModel)" in ln]
        assert not indented, f"{m} is nested inside a function"


def test_new_feature_modules_keep_bounded_responsibilities():
    # Guard new modules; legacy large files migrate incrementally, not via unrelated rewrites.
    root = Path(__file__).resolve().parents[1]
    modules = ["visual/model_routes.py", "visual/notification_routes.py", "visual/notifications.py",
               "visual/label_import.py", "visual/audit.py", "web/js/pages/models.js",
               "web/js/pages/settings.js"]
    for name in modules:
        assert len((root / name).read_text(encoding="utf-8").splitlines()) <= 500, name
