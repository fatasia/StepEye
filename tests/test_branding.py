from pathlib import Path
import struct

ROOT = Path(__file__).resolve().parents[1]

def test_brand_name_and_links():
    for path in (ROOT / 'web').rglob('*'):
        if path.suffix in {'.html', '.js'}:
            assert '视觉实验室' not in path.read_text(encoding='utf-8')
    html = (ROOT / 'web/index.html').read_text(encoding='utf-8')
    for asset in ('stepeye-icon.svg', 'stepeye.ico', 'stepeye-180.png'):
        assert f'/assets/{asset}' in html
        assert (ROOT / 'web/assets' / asset).is_file()

def test_windows_icon_sizes_and_build_config():
    data = (ROOT / 'web/assets/stepeye.ico').read_bytes()
    reserved, kind, count = struct.unpack_from('<HHH', data)
    assert (reserved, kind, count) == (0, 1, 7)
    sizes = {data[6 + i * 16] or 256 for i in range(count)}
    assert sizes == {16, 24, 32, 48, 64, 128, 256}
    assert '--icon "web/assets/stepeye.ico"' in (ROOT / 'deploy/build_windows.bat').read_text(encoding='utf-8')
