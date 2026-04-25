Set shell = CreateObject("WScript.Shell")
shell.CurrentDirectory = "C:\work\trading-journal"
shell.Run "cmd /k python ingest.py --reset", 1, False
