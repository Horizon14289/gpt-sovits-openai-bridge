Set sh = CreateObject("WScript.Shell")
sh.CurrentDirectory = "C:\path\to\gpt-sovits-openai-bridge"
sh.Run "pythonw.exe bridge.py", 0, False
