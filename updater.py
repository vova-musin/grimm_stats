#!/usr/bin/env python3
"""
Небольшой обновлятор: ждёт завершения целевого приложения, подменяет exe и запускает его.
Использование:
  updater.exe --app-path "C:\\path\\GrimmStats.exe" --source-exe "C:\\path\\new.exe" [--backup]
"""
import argparse
import os
import shutil
import subprocess
import sys
import time
import ctypes


def _log(msg: str) -> None:
    try:
        base = os.getenv('APPDATA') or os.path.expanduser('~')
        # Новое имя приложения: GrimmStats. Переносим старую папку при необходимости
        new_dir = os.path.join(base, 'GrimmStats')
        old_dir = os.path.join(base, 'MajesticRPStats')
        try:
            if os.path.isdir(old_dir) and not os.path.isdir(new_dir):
                os.rename(old_dir, new_dir)
        except Exception:
            pass
        path = new_dir
        os.makedirs(path, exist_ok=True)
        fp = os.path.join(path, 'updater.log')
        with open(fp, 'a', encoding='utf-8') as f:
            from datetime import datetime as _dt
            f.write(f"{_dt.now().isoformat(timespec='seconds')} [updater] {msg}\n")
    except Exception:
        pass


def is_file_locked(path: str) -> bool:
    try:
        if not os.path.exists(path):
            return False
        with open(path, 'rb'):
            return False
    except Exception:
        return True


def wait_for_unlock(path: str, timeout_sec: int = 60) -> bool:
    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        if not is_file_locked(path):
            return True
        time.sleep(0.2)
    return not is_file_locked(path)


def replace_file(target: str, source: str, backup: bool = True) -> None:
    os.makedirs(os.path.dirname(target), exist_ok=True)
    # Подождём освобождения файла
    _log(f"wait unlock: {target}")
    wait_for_unlock(target, 120)

    # Резервная копия
    if backup and os.path.exists(target):
        try:
            shutil.copy2(target, target + '.bak')
            _log("backup created")
        except Exception:
            _log("backup create failed")

    # Жёсткая замена
    tmp_target = target + '.tmp'
    if os.path.exists(tmp_target):
        try:
            os.remove(tmp_target)
        except Exception:
            pass
    shutil.copy2(source, tmp_target)
    # Несколько попыток замены (на случай блокировки синхронизатора/антивируса)
    last_err: Exception | None = None
    for attempt in range(1, 61):
        try:
            os.replace(tmp_target, target)
            _log(f"replace success on attempt {attempt}")
            last_err = None
            break
        except Exception as e:
            last_err = e
            if attempt in (1, 10, 30, 60):
                _log(f"replace failed (attempt {attempt}): {e}")
            time.sleep(1.0)
    if last_err is not None:
        # На случай упорной блокировки — пробуем прямую копию
        try:
            shutil.copy2(source, target)
            _log("copy2 fallback success")
            last_err = None
        except Exception as e2:
            _log(f"copy2 fallback failed: {e2}")
            last_err = e2

    # Если всё ещё не удалось — планируем замену после перезагрузки (MoveFileEx DELAY_UNTIL_REBOOT)
    if last_err is not None:
        try:
            MOVEFILE_REPLACE_EXISTING = 0x1
            MOVEFILE_DELAY_UNTIL_REBOOT = 0x4
            ctypes.windll.kernel32.MoveFileExW(tmp_target, target, MOVEFILE_REPLACE_EXISTING | MOVEFILE_DELAY_UNTIL_REBOOT)
            _log("scheduled replace on reboot (MoveFileEx)")
        except Exception as e3:
            _log(f"schedule replace failed: {e3}")
            raise last_err


def restore_backup(target: str) -> bool:
    """Пытается восстановить файл из резервной копии target.bak.
    Возвращает True при успешном восстановлении."""
    bak = target + '.bak'
    if not os.path.exists(bak):
        return False
    # На всякий случай дождёмся освобождения целевого файла
    try:
        wait_for_unlock(target, 10)
    except Exception:
        pass
    try:
        os.replace(bak, target)
        return True
    except Exception:
        try:
            shutil.copy2(bak, target)
            return True
        except Exception:
            return False


def main() -> int:
    # Мягкая обработка запуска без параметров (двойной клик)
    if len(sys.argv) == 1:
        try:
            ctypes.windll.user32.MessageBoxW(None,
                                             "Этот файл — сервис обновления и должен запускаться приложением автоматически.",
                                             "Updater", 0x40)
        except Exception:
            pass
        return 0

    parser = argparse.ArgumentParser(add_help=True)
    parser.add_argument('--app-path', required=False, help='Путь к обновляемому exe')
    parser.add_argument('--source-exe', required=False, help='Путь к новому exe (временный/локальный)')
    parser.add_argument('--backup', action='store_true', default=True)
    parser.add_argument('--start-args', default='')
    args = parser.parse_args()
    _log(f"start: app={args.app_path} source={args.source_exe}")

    if not args.app_path or not args.source_exe:
        try:
            ctypes.windll.user32.MessageBoxW(None,
                                             "Не заданы параметры --app-path и/или --source-exe. Обновлятор должен вызываться приложением.",
                                             "Updater", 0x10)
        except Exception:
            pass
        return 1

    target = os.path.abspath(args.app_path)
    source = os.path.abspath(args.source_exe)

    # Подождём завершения основного приложения (если updater запущен из него)
    time.sleep(0.5)

    try:
        replace_file(target, source, backup=bool(args.backup))
        # Исходник нам больше не нужен
        try:
            if os.path.exists(source):
                os.remove(source)
        except Exception:
            pass
    except Exception as e:
        # Выведем ошибку и завершение
        _log(f"replace error: {e}")
        return 1

    # Запустим обновлённое приложение
    try:
        cmd = [target]
        if args.start_args:
            cmd += args.start_args.split(' ')
        subprocess.Popen(cmd, close_fds=True)
        _log("started updated app")
        # Удалим резервную копию после успешного старта
        try:
            bak = target + '.bak'
            if os.path.exists(bak):
                os.remove(bak)
        except Exception:
            pass
    except Exception as e:
        _log(f"start error: {e}")
        # Попробуем откатиться на резервную копию и запустить её
        try:
            if restore_backup(target):
                cmd = [target]
                if args.start_args:
                    cmd += args.start_args.split(' ')
                subprocess.Popen(cmd, close_fds=True)
                _log("rollback to backup and started")
                return 0
        except Exception as e2:
            _log(f"rollback error: {e2}")
        return 2
    return 0


if __name__ == '__main__':
    sys.exit(main())


