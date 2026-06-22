Set ws = CreateObject("Wscript.Shell")
' Get the directory path where this VBS script is located
strPath = CreateObject("Scripting.FileSystemObject").GetParentFolderName(Wscript.ScriptFullName)
' Set working directory to the project folder to ensure relative paths resolve correctly
ws.CurrentDirectory = strPath
' Run run.bat silently (0 hides the window, False means don't block and wait)
ws.Run "cmd /c run.bat", 0, False
