import ctypes
import win32gui
import win32process

user32 = ctypes.windll.user32
hdesk = user32.OpenInputDesktop(0, False, 0x0100)
user32.SetThreadDesktop(hdesk)

fg = 1114826
tid, pid = win32process.GetWindowThreadProcessId(fg)
print(f"HWND {fg}: Class='{win32gui.GetClassName(fg)}', PID={pid}")

import os
for line in os.popen(f"tasklist /fi \"PID eq {pid}\"").read().splitlines():
    print(line)
