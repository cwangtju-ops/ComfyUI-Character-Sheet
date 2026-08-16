Option Explicit
Dim shell, fso, root, resultFile, command, code, message
If MsgBox("Remove automatic ComfyUI and Gateway startup from this Windows account? Running services will remain open.", 33, "Expression Wizard Autostart") <> 1 Then WScript.Quit 0
Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
root = fso.GetParentFolderName(WScript.ScriptFullName)
resultFile = shell.ExpandEnvironmentStrings("%TEMP%") & "\expression-wizard-autostart-remove.txt"
command = "powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -Command ""& '" & root & "\scripts\Install-ExpressionWizardAutostart.ps1' -Action Remove *> '" & resultFile & "'; exit $LASTEXITCODE"""
code = shell.Run(command, 0, True)
If fso.FileExists(resultFile) Then message = fso.OpenTextFile(resultFile, 1).ReadAll Else message = "No removal result was returned."
If code = 0 Then MsgBox message, 64, "Expression Wizard Autostart" Else MsgBox message, 16, "Expression Wizard Autostart"
WScript.Quit code
