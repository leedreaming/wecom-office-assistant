' Auto-start launcher for WeCom Office Assistant
' Registered in Windows Task Scheduler as: WeComOfficeAssistant-Autostart
' Kept 100% pure ASCII on purpose: cscript/wscript parse .vbs as ANSI, so UTF-8
' non-ASCII comments/strings can corrupt the script (unterminated string errors).
'
' Logic:
'   1. locate pythonw.exe: WECOM_VENV env -> <project>\.venv -> C:\wecom-venv
'   2. if logs\bridge.pid points at a live wecom_bridge.py process -> do nothing
'   3. otherwise start wecom_bridge.py hidden (window style 0)
' Every step is appended to logs\autostart.log.
Option Explicit

Dim fso, sh, here, logDir, logFile
Set fso = CreateObject("Scripting.FileSystemObject")
Set sh = CreateObject("WScript.Shell")

here = fso.GetParentFolderName(fso.GetParentFolderName(WScript.ScriptFullName))
logDir = here & "\logs"
If Not fso.FolderExists(logDir) Then fso.CreateFolder logDir
logFile = logDir & "\autostart.log"

Sub WLog(msg)
    Dim f
    On Error Resume Next
    Set f = fso.OpenTextFile(logFile, 8, True)
    If Not f Is Nothing Then
        f.WriteLine Now & "  " & msg
        f.Close
    End If
    On Error GoTo 0
End Sub

Dim pyw, envVenv
WLog "=== autostart begin ==="
WLog "script here=" & here

' ---------- 1) locate pythonw ----------
pyw = ""
envVenv = sh.ExpandEnvironmentStrings("%WECOM_VENV%")
If envVenv <> "%WECOM_VENV%" And envVenv <> "" Then
    If fso.FileExists(envVenv & "\Scripts\pythonw.exe") Then
        pyw = envVenv & "\Scripts\pythonw.exe"
    End If
End If
If pyw = "" Then
    If fso.FileExists(here & "\.venv\Scripts\pythonw.exe") Then
        pyw = here & "\.venv\Scripts\pythonw.exe"
    End If
End If
If pyw = "" Then
    If fso.FileExists("C:\wecom-venv\Scripts\pythonw.exe") Then
        pyw = "C:\wecom-venv\Scripts\pythonw.exe"
    End If
End If
If pyw = "" Then
    WLog "ERROR runtime not found (WECOM_VENV / .venv / C:\wecom-venv), run setup.cmd first"
    WScript.Quit 1
End If
WLog "pythonw=" & pyw

' ---------- 2) single-instance guard ----------
Dim pidFile, pid, wmi, procs, proc, alive
pidFile = here & "\logs\bridge.pid"
If fso.FileExists(pidFile) Then
    pid = ""
    On Error Resume Next
    Dim fpid
    Set fpid = fso.OpenTextFile(pidFile, 1)
    If Not fpid Is Nothing Then
        pid = Trim(fpid.ReadAll)
        fpid.Close
    End If
    On Error GoTo 0
    WLog "bridge.pid=[" & pid & "]"
    If Len(pid) > 0 Then
        If IsNumeric(pid) Then
            alive = False
            On Error Resume Next
            Set wmi = GetObject("winmgmts:\\.\root\cimv2")
            Set procs = wmi.ExecQuery("SELECT ProcessId, CommandLine FROM Win32_Process WHERE ProcessId=" & CStr(pid))
            If Err.Number = 0 Then
                For Each proc In procs
                    If InStr(1, proc.CommandLine, "wecom_bridge", 1) > 0 Then alive = True
                Next
            End If
            On Error GoTo 0
            If alive Then
                WLog "already running, pid=" & pid & " - skip start (no duplicate)"
                WScript.Quit 0
            End If
            WLog "pid=" & pid & " is not alive / not wecom_bridge - starting fresh"
        End If
    End If
Else
    WLog "no bridge.pid (first start)"
End If

' ---------- 3) start hidden ----------
sh.CurrentDirectory = here
On Error Resume Next
sh.Run """" & pyw & """ """ & here & "\wecom_bridge.py""", 0, False
If Err.Number <> 0 Then
    WLog "ERROR start failed: " & Err.Number & " " & Err.Description
    WScript.Quit 1
End If
On Error GoTo 0
WLog "started hidden: " & pyw & " " & here & "\wecom_bridge.py"
WLog "=== autostart done ==="
WScript.Quit 0
