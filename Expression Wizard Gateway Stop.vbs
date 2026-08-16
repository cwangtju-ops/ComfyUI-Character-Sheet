Option Explicit
Dim shell, root, command, code
If MsgBox("Stop the desktop ComfyUI gateway?", 33, "Expression Wizard Gateway") <> 1 Then WScript.Quit 0
Set shell = CreateObject("WScript.Shell")
root = CreateObject("Scripting.FileSystemObject").GetParentFolderName(WScript.ScriptFullName)
command = "powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File """ & root & "\scripts\Manage-ExpressionWizard.ps1"" -Component Gateway -Action Stop"
code = shell.Run(command, 0, True)
If code <> 0 Then MsgBox "The gateway is busy or could not stop safely. Finish the active experiment and try again.", 48, "Expression Wizard Gateway"
