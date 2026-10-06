' Launches Filmocity without a console window (Windows). Double-click; the browser opens when the server is ready.
Set sh = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
here = fso.GetParentFolderName(WScript.ScriptFullName)
sh.CurrentDirectory = here
py = here & "\.venv\Scripts\pythonw.exe"
If Not fso.FileExists(py) Then
  MsgBox "Supply reviewed FFmpeg and ffprobe, then run 'Install Filmocity.bat' with CPython 3.13.16 or newer on Python 3.13 (standard GIL, x64) first. See packaging/README.md.", 48, "Filmocity"
  WScript.Quit
End If
If sh.Run("""" & py & """ """ & here & "\packaging\runtime_policy.py""", 0, True) <> 0 Then
  MsgBox "This source environment requires CPython 3.13.16 or newer on Python 3.13 (standard GIL, x64). The existing .venv was preserved. Use a fresh source environment, or preserve and rename the old .venv deliberately before running Install Filmocity.bat again.", 48, "Filmocity"
  WScript.Quit 1
End If
' UTF-8 mode, added 2026-09-07, for the same reason as Filmocity.bat:
' backend reads text files with the platform default encoding in 66
' places, which is cp1252 here, and index.html is UTF-8. Without this
' the server starts and every page is "Internal Server Error".
Set env = sh.Environment("PROCESS")
env("PYTHONUTF8") = "1"
sh.Run """" & py & """ """ & here & "\launcher\bootstrap.py"" run", 0, False
