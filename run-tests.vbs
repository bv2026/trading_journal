Set shell = CreateObject("WScript.Shell")
shell.CurrentDirectory = "C:\work\trading-journal"
shell.Run "cmd /k python -m pytest tests/ -v", 1, False
