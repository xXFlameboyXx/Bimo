import ctypes
import time
import win32gui
import win32process

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32
hdesk = user32.OpenInputDesktop(0, False, 0x0100)
user32.SetThreadDesktop(hdesk)

target = 330692
fg = user32.GetForegroundWindow()
fg_thread, fg_pid = win32process.GetWindowThreadProcessId(fg)
target_thread, target_pid = win32process.GetWindowThreadProcessId(target)
cur_thread = kernel32.GetCurrentThreadId()

print(f"cur_thread={cur_thread}, fg_thread={fg_thread} (PID {fg_pid}), target_thread={target_thread} (PID {target_pid})")

# Check if AttachThreadInput succeeds
res_att1 = user32.AttachThreadInput(cur_thread, fg_thread, True)
err1 = kernel32.GetLastError()
print(f"Attach to fg_thread: res={res_att1}, err={err1}")

res_att2 = user32.AttachThreadInput(cur_thread, target_thread, True)
err2 = kernel32.GetLastError()
print(f"Attach to target_thread: res={res_att2}, err={err2}")

# Try unlock
user32.AllowSetForegroundWindow(-1)

# Alt key
user32.keybd_event(0x12, 0, 0, 0)
user32.keybd_event(0x12, 0, 2, 0)

res_show = user32.ShowWindow(target, 9)
res_set = user32.SetForegroundWindow(target)
err_set = kernel32.GetLastError()
print(f"SetForegroundWindow: res={res_set}, err={err_set}")

user32.AttachThreadInput(cur_thread, fg_thread, False)
user32.AttachThreadInput(cur_thread, target_thread, False)

time.sleep(0.3)
new_fg = user32.GetForegroundWindow()
print(f"New FG: {new_fg} (Matches target: {new_fg == target})")
