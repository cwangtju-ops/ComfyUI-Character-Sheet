Option Explicit
Dim shell, fso, root, resultFile, command, code, message
Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
root = fso.GetParentFolderName(WScript.ScriptFullName)
resultFile = shell.ExpandEnvironmentStrings("%TEMP%") & "\expression-wizard-laptop-status.txt"
command = "powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File """ & root & "\scripts\Manage-ExpressionWizard.ps1"" -Component Laptop -Action Status -ResultFile """ & resultFile & """"
code = shell.Run(command, 0, True)
If fso.FileExists(resultFile) Then
  message = fso.OpenTextFile(resultFile, 1).ReadAll
Else
  message = "Status could not be read."
End If
MsgBox message, 64, "Expression Wizard Laptop Status"
