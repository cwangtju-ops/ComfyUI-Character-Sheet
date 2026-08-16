Option Explicit
Dim shell, root, command, code
If MsgBox("Stop the Expression Wizard laptop backend? Active experiments will not be interrupted.", 33, "Expression Wizard") <> 1 Then WScript.Quit 0
Set shell = CreateObject("WScript.Shell")
root = CreateObject("Scripting.FileSystemObject").GetParentFolderName(WScript.ScriptFullName)
command = "powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File """ & root & "\scripts\Manage-ExpressionWizard.ps1"" -Component Laptop -Action Stop"
code = shell.Run(command, 0, True)
If code <> 0 Then MsgBox "The backend was not stopped. It may still be generating. Use the Stop backend button in EW to cancel safely, or inspect the logs.", 48, "Expression Wizard"
