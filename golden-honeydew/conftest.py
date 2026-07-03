"""pytest 루트 conftest.

uv virtual project 방식이라 polybot 패키지가 site-packages에 설치되지 않는다.
main.py와 동일하게 src/를 sys.path에 추가해 tests에서 import 가능하게 한다.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))
