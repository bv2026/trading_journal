Set shell = CreateObject("WScript.Shell")
shell.Run "taskkill /F /IM streamlit.exe", 0, True
