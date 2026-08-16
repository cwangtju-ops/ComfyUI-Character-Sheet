Option Explicit
Dim shell, root, command, code
Set shell = CreateObject("WScript.Shell")
root = CreateObject("Scripting.FileSystemObject").GetParentFolderName(WScript.ScriptFullName)
command = "powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File """ & root & "\scripts\Manage-ExpressionWizard.ps1"" -Component Gateway -Action Start"
code = shell.Run(command, 0, True)
If code <> 0 Then MsgBox "The ComfyUI gateway could not start. Open .expression_wizard\runtime\gateway in your user folder to see the logs.", 16, "Expression Wizard Gateway"
