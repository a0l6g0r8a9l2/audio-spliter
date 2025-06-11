import argparse
import os
import subprocess
import sys
import re
import math
import shutil
from pathlib import Path
from tqdm import tqdm

def split_audio_by_size(input_file: str, chunk_size_mb: float = 20.0) -> list[str]:
    """
    Извлекает аудиодорожку из медиафайла, конвертирует ее в формат FLAC
    и нарезает на части заданного размера.

    Args:
        input_file (str): Полный путь к исходному файлу.
        chunk_size_mb (float): Желаемый размер каждой части в мегабайтах.

    Returns:
        list[str]: Список полных путей к созданным аудиофайлам.
        
    Raises:
        FileNotFoundError: Если исходный файл не найден.
        RuntimeError: Если ffmpeg/ffprobe не найдены или произошла ошибка в процессе.
    """
    # --- 1. Проверка наличия ffmpeg и файла ---
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        raise RuntimeError("Ошибка: FFmpeg и/или FFprobe не найдены. Убедитесь, что они установлены и доступны в системном PATH.")

    source_path = Path(input_file)
    if not source_path.is_file():
        raise FileNotFoundError(f"Ошибка: Файл не найден по пути: {input_file}")

    # --- 2. Создание выходной директории ---
    output_dir = source_path.parent / f"{source_path.stem}_chunks"
    output_dir.mkdir(exist_ok=True)
    print(f"🗂️  Части будут сохранены в: {output_dir}")

    # --- 3. Получение общей длительности аудио ---
    try:
        duration_cmd = [
            "ffprobe",
            "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            str(source_path)
        ]
        result = subprocess.run(duration_cmd, capture_output=True, text=True, check=True)
        total_duration = float(result.stdout.strip())
    except (subprocess.CalledProcessError, ValueError) as e:
        raise RuntimeError(f"Ошибка получения длительности аудио из файла: {e.stderr or e}")

    # --- 4. Оценка времени сегмента на основе размера ---
    # Для точного расчета нам нужно знать битрейт конечного FLAC файла.
    # Создадим временный полный FLAC файл, чтобы рассчитать точное соотношение байт/секунда.
    temp_flac_path = output_dir / f"temp_{source_path.stem}.flac"
    
    print("⏳ Анализ аудио (может занять некоторое время)...")
    try:
        # Конвертируем весь файл в один временный FLAC
        analyze_cmd = [
            "ffmpeg",
            "-y",  # Перезаписать без вопроса
            "-i", str(source_path),
            "-map", "0:a",
            "-ac", "1",
            "-ar", "16000",
            "-c:a", "flac",
            str(temp_flac_path)
        ]
        subprocess.run(analyze_cmd, check=True, capture_output=True)
        
        # Получаем его точный размер
        temp_file_size_bytes = temp_flac_path.stat().st_size
        
        # Рассчитываем соотношение
        bytes_per_second = temp_file_size_bytes / total_duration
        chunk_size_bytes = chunk_size_mb * 1024 * 1024
        segment_duration = math.floor(chunk_size_bytes / bytes_per_second)

        if segment_duration <= 0:
            segment_duration = 60 # Минимальная продолжительность, если расчет некорректен
        
        print(f"✅ Анализ завершен. Длительность каждого сегмента: ~{segment_duration} сек.")

    except subprocess.CalledProcessError as e:
        raise RuntimeError(f"Ошибка при анализе аудио: {e.stderr.decode('utf-8')}")
    finally:
        # Удаляем временный файл
        if temp_flac_path.exists():
            temp_flac_path.unlink()

    # --- 5. Основной процесс нарезки ---
    output_pattern = output_dir / "part_%03d.flac"
    
    split_cmd = [
        "ffmpeg",
        "-i", str(source_path),
        "-map", "0:a",        # Только аудио
        "-ac", "1",           # Моно
        "-ar", "16000",       # Ресемплинг 16000 Гц
        "-c:a", "flac",       # Кодек FLAC
        "-f", "segment",      # Использовать сегментацию
        "-segment_time", str(segment_duration),
        "-reset_timestamps", "1",
        str(output_pattern)
    ]

    print(f"🚀 Запуск нарезки файла...")
    try:
        # Используем Popen для отслеживания прогресса в реальном времени
        process = subprocess.Popen(split_cmd, stderr=subprocess.PIPE, stdout=subprocess.DEVNULL, text=True, encoding='utf-8')

        # Настройка прогресс-бара tqdm
        with tqdm(total=total_duration, unit='s', desc="Прогресс", bar_format="{l_bar}{bar}| {n:.2f}/{total:.2f}s") as pbar:
            previous_time = 0
            for line in process.stderr:
                # Ищем строку с временем в выводе ffmpeg
                match = re.search(r"time=(\d{2}):(\d{2}):(\d{2})\.(\d{2})", line)
                if match:
                    h, m, s, ms = map(int, match.groups())
                    current_time = h * 3600 + m * 60 + s + ms / 100
                    pbar.update(current_time - previous_time)
                    previous_time = current_time
            
            # Убедимся, что прогресс-бар дошел до конца
            if pbar.n < total_duration:
                 pbar.update(total_duration - pbar.n)


        process.wait()
        if process.returncode != 0:
            # Если возникла ошибка, stderr уже был прочитан, нужно его как-то сохранить и вывести
            # В данном случае, основной вывод ошибки будет в логах выше
            raise RuntimeError(f"FFmpeg завершился с ошибкой (код {process.returncode}). Проверьте лог выше.")

    except Exception as e:
        print(f"\n❌ Произошла ошибка во время нарезки: {e}", file=sys.stderr)
        return []

    # --- 6. Возврат списка файлов ---
    chunk_files = sorted([str(f) for f in output_dir.glob("part_*.flac")])
    print(f"\n✅ Нарезка успешно завершена! Создано файлов: {len(chunk_files)}")
    return chunk_files


def main():
    """Основная функция для запуска из командной строки."""
    parser = argparse.ArgumentParser(
        description="Извлекает аудиодорожку из файла и делит ее на части заданного размера.",
        formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument(
        "input_file",
        type=str,
        help="Полный путь к исходному видео или аудио файлу."
    )
    parser.add_argument(
        "-s", "--size",
        type=float,
        default=20.0,
        help="Размер каждой части в мегабайтах (МБ). По-умолчанию: 20."
    )

    args = parser.parse_args()

    try:
        created_files = split_audio_by_size(args.input_file, args.size)
        if created_files:
            print("\nСписок созданных файлов:")
            for f in created_files:
                print(f"  - {f}")
    except (FileNotFoundError, RuntimeError) as e:
        print(f"\n❌ Критическая ошибка: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
