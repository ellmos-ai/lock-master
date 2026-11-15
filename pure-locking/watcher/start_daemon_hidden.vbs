' start_daemon_hidden.vbs -- starts the lock-master watcher daemon without a console window.
'
' Usage:   wscript //B //Nologo start_daemon_hidden.vbs [--update-cache]
'          (arguments are passed on to lock_watcher.py)
'
' Why a VBS wrapper instead of calling pythonw from a scheduled task: only this way can stdout
' go to a log file without a cmd window flashing up (window style 0).
'
' Environment (set it at user level so a scheduled task inherits it):
'   LOCK_MASTER_PYTHONW        full path of pythonw.exe (default: pythonw.exe from PATH)
'   LOCK_MASTER_WATCHER_DATA   runtime directory (default: %USERPROFILE%\.lock_master_watcher);
'                              the log daemon_stdout.log lives there as well
'   LOCK_MASTER_ROOTS_FILE, LOCK_MASTER_WATCHER_CACHE_DIR, ... are read by the daemon itself.
'
' A second start is harmless: lock_watcher.run_daemon() detects a running daemon by its fresh
' heartbeat and exits at once, so a repeating task doubles as a watchdog.

Option Explicit

Dim fso, sh, dir, py, script, dataDir, logDir, logFile, cmd, maxBytes, args, i

Set fso = CreateObject("Scripting.FileSystemObject")
Set sh  = CreateObject("WScript.Shell")

dir    = fso.GetParentFolderName(WScript.ScriptFullName)
script = fso.BuildPath(dir, "lock_watcher.py")

py = sh.ExpandEnvironmentStrings("%LOCK_MASTER_PYTHONW%")
If py = "%LOCK_MASTER_PYTHONW%" Or py = "" Then py = "pythonw.exe"

dataDir = sh.ExpandEnvironmentStrings("%LOCK_MASTER_WATCHER_DATA%")
If dataDir = "%LOCK_MASTER_WATCHER_DATA%" Or dataDir = "" Then
    dataDir = fso.BuildPath(sh.ExpandEnvironmentStrings("%USERPROFILE%"), ".lock_master_watcher")
End If
logDir  = dataDir
logFile = fso.BuildPath(logDir, "daemon_stdout.log")

If Not fso.FolderExists(logDir) Then
    fso.CreateFolder(logDir)
End If

' Simple rotation: keep one previous log once the current one exceeds 5 MB.
maxBytes = 5 * 1024 * 1024
If fso.FileExists(logFile) Then
    If fso.GetFile(logFile).Size > maxBytes Then
        If fso.FileExists(logFile & ".1") Then fso.DeleteFile logFile & ".1", True
        fso.MoveFile logFile, logFile & ".1"
    End If
End If

args = ""
For i = 0 To WScript.Arguments.Count - 1
    args = args & " " & WScript.Arguments(i)
Next

' -u = unbuffered: with output redirected to a file Python buffers block-wise, and a hard crash
' would lose exactly the buffer that holds the traceback.
cmd = "cmd /c """"" & py & """ -u """ & script & """" & args & " >> """ & logFile & """ 2>&1"""

' 0 = no window, True = wait for the daemon to end. Waiting is deliberate: if the caller (task
' scheduler) sees the wrapper return at once, the action counts as finished and the started
' process tree can be torn down with it. The task must therefore have no ExecutionTimeLimit.
sh.Run cmd, 0, True
