@echo off
echo Starting ai-tutor...

CALL "%USERPROFILE%\anaconda3\Scripts\activate.bat" ai-tutor
cd /d "%USERPROFILE%\Desktop\AI-Language-Tutor\"
python desktop.py
pause
