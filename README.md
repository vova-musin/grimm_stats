# MajesticRP Статистика (Дальнобойщик)

Приложение на Python (PySide6) для учёта рабочего времени, доходов и расходов.

Теперь добавлены:
- График в разделе Статистика с двумя осями: синяя — чистая прибыль по периоду, красная — заработок в час.
- Подпериоды: 1 день, 7 дней, 30 дней (переключаются вкладками).
- На вкладке Дальнобойщик: блок «Итого за сессию» (для текущей либо последней).

## Запуск (Windows)
1. Установите Python 3.10+.
2. В консоли PowerShell в папке проекта выполните:
```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
python main.py
```

## Сборка EXE (PyInstaller)
### Автоматически
```powershell
powershell -ExecutionPolicy Bypass -File .\build.ps1
```
Готовый файл появится в `dist/MajesticRPStats.exe`.

## Зависимости
- PySide6 — GUI
- matplotlib — графики
- PyInstaller — сборка EXE

## Данные
Папка `data/`, по одному JSON на день `YYYY-MM-DD.json`.
