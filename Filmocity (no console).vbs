' Launches Filmocity without a console window (Windows). Double-click; the browser opens when the server is ready.
Set sh = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
here = fso.GetParentFolderName(WScript.ScriptFullName)
sh.CurrentDirectory = here
py = here & "\.venv\Scripts\pythonw.exe"
If Not fso.FileExists(py) Then
  MsgBox "Run 'Install Filmocity.bat' first (it creates .venv and downloads FFmpeg).", 48, "Filmocity"
  WScript.Quit
End If
' UTF-8 mode, added 2026-09-07, for the same reason as Filmocity.bat:
' backend reads text files with the platform default encoding in 66
' places, which is cp1252 here, and index.html is UTF-8. Without this
' the server starts and every page is "Internal Server Error".
Set env = sh.Environment("PROCESS")
env("PYTHONUTF8") = "1"
sh.Run """" & py & """ """ & here & "\launcher\bootstrap.py"" run", 0, False
