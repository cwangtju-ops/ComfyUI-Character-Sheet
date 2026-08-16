Option Explicit
Dim shell, root, command, code
Set shell = CreateObject("WScript.Shell")
root = CreateObject("Scripting.FileSystemObject").GetParentFolderName(WScript.ScriptFullName)
command = "powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File """ & root & "\scripts\Manage-ExpressionWizard.ps1"" -Component Laptop -Action Start -OpenBrowser"
code = shell.Run(command, 0, True)
If code <> 0 Then MsgBox "Expression Wizard could not start. Open .expression_wizard\runtime\laptop in your user folder to see the logs.", 16, "Expression Wizard"
