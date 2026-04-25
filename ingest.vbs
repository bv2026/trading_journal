Set shell = CreateObject("WScript.Shell")
shell.CurrentDirectory = "C:\work\trading-journal"
shell.Run "cmd /k python ingest.py", 1, False
