"""pytest 根配置：把插件工程根目录放进 sys.path，使 test/ 能 import tools 包。"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
