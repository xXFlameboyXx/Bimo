import win32api
import win32gui
import ctypes

user32 = ctypes.windll.user32
hdesk = user32.OpenInputDesktop(0, False, 0x0100)
user32.SetThreadDesktop(hdesk)

monitors = win32api.EnumDisplayMonitors()
print(f"Total monitors: {len(monitors)}")
for i, m in enumerate(monitors):
    info = win32api.GetMonitorInfo(m[0])
    print(f"Monitor {i}: rect={info.get('Monitor')}, work={info.get('Work')}, primary={info.get('Flags') == 1}")

h = 789090
print("Target 789090 rect:", win32gui.GetWindowRect(h))
