import win32process, win32gui, ctypes

user32 = ctypes.windll.user32
hinput = user32.OpenInputDesktop(0, False, 0x0100)
user32.SetThreadDesktop(hinput)

fg = user32.GetForegroundWindow()
print("Current FG:", fg, win32gui.GetWindowText(fg))

tid, pid = win32process.GetWindowThreadProcessId(fg)
print(f"FG Thread: {tid}, FG PID: {pid}")

# Check all Notepad windows
wins = []
def enum(h, _):
    t = win32gui.GetWindowText(h)
    if "notepad" in t.lower():
        t_id, p_id = win32process.GetWindowThreadProcessId(h)
        wins.append((h, t, t_id, p_id))
win32gui.EnumDesktopWindows(hinput, enum, None)
for w in wins:
    print("Notepad:", w)
