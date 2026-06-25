@echo off
:: Проверка наличия виртуального окружения
if not exist "venv" (
    echo Creating virtual environment...
    python -m venv venv
)

:: Активация окружения и запуск программы
call venv\Scripts\activate
python voice_typer.py
pause
