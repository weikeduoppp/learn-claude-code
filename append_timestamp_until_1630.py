from pathlib import Path
from datetime import datetime
import time

out = Path(".temp.txt")
deadline = datetime.now().replace(hour=16, minute=30, second=0, microsecond=0)
if datetime.now() >= deadline:
    print("deadline passed")
    raise SystemExit(0)

while datetime.now() < deadline:
    with out.open("a", encoding="utf-8") as f:
        f.write(datetime.now().isoformat() + "\n")
    time.sleep(3)

print("done")
