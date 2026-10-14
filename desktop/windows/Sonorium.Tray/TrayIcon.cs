using System.Runtime.InteropServices;

namespace Sonorium.Tray;

/// <summary>
/// The notification-area icon (Shell_NotifyIcon) with a hidden message window. Left click and
/// right click are reported; Explorer restarting (TaskbarCreated) puts the icon back.
/// </summary>
public sealed class TrayIcon : IDisposable
{
    const int WM_USER = 0x0400, WM_TRAY = WM_USER + 1;
    const int WM_LBUTTONUP = 0x0202, WM_RBUTTONUP = 0x0205, WM_CONTEXTMENU = 0x007B, NIN_SELECT = WM_USER, NIN_KEYSELECT = WM_USER + 1;
    const int NIM_ADD = 0, NIM_MODIFY = 1, NIM_DELETE = 2, NIM_SETVERSION = 4;
    const int NIF_MESSAGE = 1, NIF_ICON = 2, NIF_TIP = 4, NIF_SHOWTIP = 0x80;
    const int NOTIFYICON_VERSION_4 = 4;

    // A plain id, not a GUID: Windows ties a GUID icon to the first program path that used it, so
    // a second copy of Sonorium.exe (another install folder) could never show its icon
    const int IconId = 1;
    const int WM_TIMER = 0x0113, RetryTimer = 1;
    int retries;
    readonly WndProc proc;
    readonly IntPtr hwnd;
    readonly uint taskbarCreated;
    IntPtr icon;
    string tip = "Sonorium";

    public event Action? LeftClick;
    public event Action? RightClick;
    /// <summary>A Windows setting changed (e.g. light/dark taskbar): the icon may need to change.</summary>
    public event Action? SettingsChanged;

    public TrayIcon(IntPtr hicon)
    {
        icon = hicon;
        proc = WindowProc;
        var cls = new WNDCLASSEX
        {
            cbSize = Marshal.SizeOf<WNDCLASSEX>(),
            lpfnWndProc = Marshal.GetFunctionPointerForDelegate(proc),
            hInstance = GetModuleHandle(null),
            lpszClassName = "Sonorium.TrayIcon",
        };
        RegisterClassEx(ref cls);
        hwnd = CreateWindowEx(0, cls.lpszClassName, "Sonorium", 0, 0, 0, 0, 0, IntPtr.Zero, IntPtr.Zero, cls.hInstance, IntPtr.Zero);
        taskbarCreated = RegisterWindowMessage("TaskbarCreated");
        Add();
    }

    public void SetIcon(IntPtr hicon)
    {
        icon = hicon;
        var data = Data(NIF_ICON);
        Shell_NotifyIcon(NIM_MODIFY, ref data);
    }

    public void SetTip(string text)
    {
        tip = text.Length > 127 ? text[..127] : text;
        var data = Data(NIF_TIP | NIF_SHOWTIP);
        Shell_NotifyIcon(NIM_MODIFY, ref data);
    }

    /// <summary>Adds the icon; while the taskbar isn't ready (early at sign-in) it tries again every 2 s for a minute.</summary>
    void Add()
    {
        var data = Data(NIF_MESSAGE | NIF_ICON | NIF_TIP | NIF_SHOWTIP);
        Shell_NotifyIcon(NIM_DELETE, ref data);
        if (!Shell_NotifyIcon(NIM_ADD, ref data))
        {
            if (retries++ < 30) SetTimer(hwnd, RetryTimer, 2000, IntPtr.Zero);
            return;
        }
        KillTimer(hwnd, RetryTimer);
        retries = 0;
        data.uVersion = NOTIFYICON_VERSION_4;
        Shell_NotifyIcon(NIM_SETVERSION, ref data);
    }

    NOTIFYICONDATA Data(int flags) => new()
    {
        cbSize = Marshal.SizeOf<NOTIFYICONDATA>(),
        hWnd = hwnd,
        uID = IconId,
        uFlags = flags,
        uCallbackMessage = WM_TRAY,
        hIcon = icon,
        szTip = tip,
    };

    IntPtr WindowProc(IntPtr h, uint msg, IntPtr w, IntPtr l)
    {
        if (msg == WM_TRAY)
        {
            switch ((int)(l.ToInt64() & 0xFFFF))
            {
                case WM_LBUTTONUP or NIN_SELECT or NIN_KEYSELECT: LeftClick?.Invoke(); break;
                case WM_RBUTTONUP or WM_CONTEXTMENU: RightClick?.Invoke(); break;
            }
            return IntPtr.Zero;
        }
        if (msg == taskbarCreated) Add();
        if (msg == WM_TIMER && w == RetryTimer)
        {
            KillTimer(hwnd, RetryTimer);
            Add();
        }
        if (msg == 0x001A /* WM_SETTINGCHANGE */ || msg == 0x02E0 /* WM_DPICHANGED */) SettingsChanged?.Invoke();
        return DefWindowProc(h, msg, w, l);
    }

    public void Dispose()
    {
        var data = Data(0);
        Shell_NotifyIcon(NIM_DELETE, ref data);
        DestroyWindow(hwnd);
    }

    delegate IntPtr WndProc(IntPtr hwnd, uint msg, IntPtr wParam, IntPtr lParam);

    [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
    struct WNDCLASSEX
    {
        public int cbSize; public int style; public IntPtr lpfnWndProc; public int cbClsExtra; public int cbWndExtra;
        public IntPtr hInstance; public IntPtr hIcon; public IntPtr hCursor; public IntPtr hbrBackground;
        public string? lpszMenuName; public string lpszClassName; public IntPtr hIconSm;
    }

    [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
    struct NOTIFYICONDATA
    {
        public int cbSize; public IntPtr hWnd; public int uID; public int uFlags; public int uCallbackMessage; public IntPtr hIcon;
        [MarshalAs(UnmanagedType.ByValTStr, SizeConst = 128)] public string szTip;
        public int dwState; public int dwStateMask;
        [MarshalAs(UnmanagedType.ByValTStr, SizeConst = 256)] public string szInfo;
        public int uVersion;
        [MarshalAs(UnmanagedType.ByValTStr, SizeConst = 64)] public string szInfoTitle;
        public int dwInfoFlags; public Guid guidItem; public IntPtr hBalloonIcon;
    }

    [DllImport("shell32.dll", CharSet = CharSet.Unicode)] static extern bool Shell_NotifyIcon(int message, ref NOTIFYICONDATA data);
    [DllImport("user32.dll", CharSet = CharSet.Unicode)] static extern ushort RegisterClassEx(ref WNDCLASSEX cls);
    [DllImport("user32.dll", CharSet = CharSet.Unicode)]
    static extern IntPtr CreateWindowEx(int exStyle, string cls, string name, int style, int x, int y, int w, int h, IntPtr parent, IntPtr menu, IntPtr instance, IntPtr param);
    [DllImport("user32.dll")] static extern IntPtr DefWindowProc(IntPtr hwnd, uint msg, IntPtr w, IntPtr l);
    [DllImport("user32.dll")] static extern bool DestroyWindow(IntPtr hwnd);
    [DllImport("user32.dll")] static extern IntPtr SetTimer(IntPtr hwnd, IntPtr id, uint ms, IntPtr func);
    [DllImport("user32.dll")] static extern bool KillTimer(IntPtr hwnd, IntPtr id);
    [DllImport("user32.dll", CharSet = CharSet.Unicode)] static extern uint RegisterWindowMessage(string name);
    [DllImport("kernel32.dll", CharSet = CharSet.Unicode)] static extern IntPtr GetModuleHandle(string? name);
}
