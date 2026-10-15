import ctypes
import win32gui

user32 = ctypes.windll.user32
hinput = user32.OpenInputDesktop(0, False, 0x0100)
user32.SetThreadDesktop(hinput)

class POINT(ctypes.Structure):
    _fields_ = [('x', ctypes.c_long), ('y', ctypes.c_long)]

pt = POINT(300, 300)
top = user32.WindowFromPoint(pt)
root = user32.GetAncestor(top, 2)
print('Top HWND at (300, 300):', top, 'root:', root, win32gui.GetWindowText(root))
