import win32gui
import win32con
import ctypes

user32 = ctypes.windll.user32
hdesk = user32.OpenInputDesktop(0, False, 0x0100)
user32.SetThreadDesktop(hdesk)

notepads = []
def enum(h, _):
    t = win32gui.GetWindowText(h)
    if "notepad" in t.lower():
        rect = win32gui.GetWindowRect(h)
        placement = win32gui.GetWindowPlacement(h)
        notepads.append((h, t, rect, placement[1]))
win32gui.EnumDesktopWindows(hdesk, enum, None)

for n in notepads:
    # placement[1]: 1 = normal, 2 = minimized, 3 = maximized
    state = "MINIMIZED" if n[3] == 2 or n[2][0] <= -20000 else "NORMAL"
    print(f"HWND: {n[0]}, Title: '{n[1]}', Rect: {n[2]}, State: {state}")
