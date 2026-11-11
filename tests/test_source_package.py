import zipfile

from deploy import build_source_package


def test_source_package_keeps_tauri_sources_without_runtime_or_build_cache(tmp_path, monkeypatch):
    root=tmp_path/'project'
    for name in ('src/main.rs','src/.env','src/test.key','runtime/private.txt','target/cache.txt','gen/schemas/generated.json'):
        path=root/'desktop/src-tauri'/name
        path.parent.mkdir(parents=True,exist_ok=True)
        path.write_text(name,encoding='utf8')
    monkeypatch.setattr(build_source_package,'ROOT',root)
    archive=tmp_path/'source.zip'
    with zipfile.ZipFile(archive,'w') as output:
        build_source_package.add_tree(output,root/'desktop','StepEye/desktop')
    with zipfile.ZipFile(archive) as output:
        assert output.namelist()==['StepEye/desktop/src-tauri/src/main.rs']
