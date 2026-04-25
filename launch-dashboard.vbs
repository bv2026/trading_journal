Set shell = CreateObject("WScript.Shell")
shell.CurrentDirectory = "C:\work\trading-journal"
shell.Run "streamlit run dashboard\app.py", 0, False
