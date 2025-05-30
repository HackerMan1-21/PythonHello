import os
import tempfile
from component import broken_checker

def test_is_broken_image():
    # 正常画像
    from PIL import Image
    with tempfile.NamedTemporaryFile(suffix='.png', delete=False) as f:
        img = Image.new('RGB', (10, 10), color='red')
        img.save(f.name)
        temp_path = f.name
    assert broken_checker.is_broken_image(temp_path) is False
    os.remove(temp_path)
    # 壊れ画像
    with tempfile.NamedTemporaryFile(suffix='.png', delete=False) as f:
        f.write(b'not an image')
        temp_path = f.name
    assert broken_checker.is_broken_image(temp_path) is True
    os.remove(temp_path)

def test_is_broken_video():
    # 壊れ動画のみテスト
    with tempfile.NamedTemporaryFile(suffix='.mp4', delete=False) as f:
        f.write(b'not a video')
        temp_path = f.name
    assert broken_checker.is_broken_video(temp_path) is True
    os.remove(temp_path)

def test_check_broken_images_progress():
    # 進捗ログ出力のテスト（printされるだけ）
    tmpdir = tempfile.mkdtemp()
    from PIL import Image
    for i in range(5):
        img = Image.new('RGB', (10, 10), color='blue')
        img.save(os.path.join(tmpdir, f'{i}.png'))
    broken = broken_checker.check_broken_images(tmpdir, log_progress=True)
    assert isinstance(broken, list)
    for f in os.listdir(tmpdir):
        os.remove(os.path.join(tmpdir, f))
    os.rmdir(tmpdir)
