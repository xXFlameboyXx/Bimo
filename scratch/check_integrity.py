import win32process, win32api, win32con, win32security

def get_process_integrity(pid):
    try:
        handle = win32api.OpenProcess(win32con.PROCESS_QUERY_INFORMATION, False, pid)
        token = win32security.OpenProcessToken(handle, win32con.TOKEN_QUERY)
        mapping = {
            "S-1-16-4096": "Low",
            "S-1-16-8192": "Medium",
            "S-1-16-12288": "High (Admin)",
            "S-1-16-16384": "System"
        }
        for sid, attr in win32security.GetTokenInformation(token, win32security.TokenIntegrityLevel):
            sid_str = win32security.ConvertSidToStringSid(sid)
            return mapping.get(sid_str, sid_str)
    except Exception as e:
        return f"Error: {e}"

import os
print("Current Python PID:", os.getpid(), "Integrity:", get_process_integrity(os.getpid()))

# Check Discord PID
import win32gui
_, discord_pid = win32process.GetWindowThreadProcessId(132158)
print("Discord PID:", discord_pid, "Integrity:", get_process_integrity(discord_pid))

# Check Notepad PID
_, notepad_pid = win32process.GetWindowThreadProcessId(658462)
print("Notepad PID:", notepad_pid, "Integrity:", get_process_integrity(notepad_pid))
