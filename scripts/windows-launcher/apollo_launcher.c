/* Apollo.exe — double-clickable Windows launcher.
 *
 * The Windows counterpart of the macOS launcher app: it does not bundle a
 * runtime, it drives the repo it sits in. Drop Apollo.exe into the Apollo
 * folder (next to launch-windows.ps1) and double-click — it opens the
 * PowerShell bootstrap/launcher in a visible console (first run prints the
 * admin password there).
 *
 * Cross-compile from macOS/Linux:
 *   x86_64-w64-mingw32-gcc -O2 -municode -mwindows \
 *     -o Apollo.exe scripts/windows-launcher/apollo_launcher.c
 */
#include <windows.h>

int APIENTRY wWinMain(HINSTANCE hInst, HINSTANCE hPrev, PWSTR cmdLine, int nShow)
{
    (void)hInst; (void)hPrev; (void)cmdLine; (void)nShow;

    WCHAR dir[MAX_PATH];
    if (!GetModuleFileNameW(NULL, dir, MAX_PATH)) {
        MessageBoxW(NULL, L"Could not resolve the launcher path.", L"Apollo", MB_ICONERROR);
        return 1;
    }
    WCHAR *slash = wcsrchr(dir, L'\\');
    if (slash) *slash = 0;

    WCHAR script[MAX_PATH + 32];
    wsprintfW(script, L"%s\\launch-windows.ps1", dir);
    if (GetFileAttributesW(script) == INVALID_FILE_ATTRIBUTES) {
        MessageBoxW(NULL,
            L"launch-windows.ps1 not found.\n\n"
            L"Put Apollo.exe inside the Apollo folder (next to launch-windows.ps1) "
            L"and double-click it there.",
            L"Apollo", MB_ICONERROR);
        return 1;
    }

    WCHAR cmd[2 * MAX_PATH + 64];
    wsprintfW(cmd,
        L"powershell.exe -NoProfile -ExecutionPolicy Bypass -File \"%s\"",
        script);

    STARTUPINFOW si;
    PROCESS_INFORMATION pi;
    ZeroMemory(&si, sizeof(si));
    si.cb = sizeof(si);
    /* Visible console: the bootstrap prints progress and, on first run, the
     * admin password. */
    if (!CreateProcessW(NULL, cmd, NULL, NULL, FALSE, CREATE_NEW_CONSOLE,
                        NULL, dir, &si, &pi)) {
        MessageBoxW(NULL, L"Could not start PowerShell.", L"Apollo", MB_ICONERROR);
        return 1;
    }
    CloseHandle(pi.hProcess);
    CloseHandle(pi.hThread);
    return 0;
}
