import os
import tempfile
from pathlib import Path

# 必须在导入 bamy 之前指向一次性数据库，避免测试写到真实库
_tmp = tempfile.mkdtemp(prefix="bamy-test-")
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{Path(_tmp) / 'test.db'}"
