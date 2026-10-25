"""Use this fork's lazy entry points with the matching official native wheel."""
from importlib.metadata import distribution
from pathlib import Path
import shutil

dist = distribution('penaltyblog')
if dist.version != '1.13.1': raise RuntimeError('requires_penaltyblog_1_13_1')
root = Path(__file__).resolve().parents[2]
for relative in ['penaltyblog/__init__.py','penaltyblog/models/__init__.py']:
    source, target = root/relative, Path(dist.locate_file(relative))
    if source.resolve() == target.resolve(): continue
    backup = target.with_suffix('.py.before-dnsgon')
    if not backup.exists(): shutil.copy2(target,backup)
    shutil.copy2(source,target)
print('Installed lazy entry points; native model kernels remain the official 1.13.1 wheel.')
