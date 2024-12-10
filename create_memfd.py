import os
import sys
import time

if len(sys.argv) != 2:
    print("Usage: python3 create_memfd.py <name>")
    sys.exit(1)

fd = os.memfd_create(sys.argv[1], os.MFD_ALLOW_SEALING)
print(f"Created memfd with fd: {fd}")
print(f"File: /proc/self/fd/{fd}")
time.sleep(3600)  # Keep running to maintain the fd
