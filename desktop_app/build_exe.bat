@echo off
echo ========================================
echo Building Hacienda SFTP to S3 Desktop App
echo ========================================
echo.

REM Check if Python is installed
python --version >nul 2>&1
if errorlevel 1 (
    echo ERROR: Python is not installed or not in PATH
    pause
    exit /b 1
)

REM Install requirements
echo Installing dependencies...
pip install -r requirements.txt
pip install pyinstaller

echo.
echo Building executable...
pyinstaller --onefile --windowed --name "Hacienda_SFTP_Download" --icon=NONE sftp_to_s3_app.py

echo.
echo ========================================
echo Build complete!
echo Executable location: dist\Hacienda_SFTP_Download.exe
echo ========================================
pause
