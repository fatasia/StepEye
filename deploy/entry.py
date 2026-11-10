"""PyInstaller 打包入口:exe 双击等价 `python -m visual`。"""
import sys

from visual.cli import main

sys.exit(main())
