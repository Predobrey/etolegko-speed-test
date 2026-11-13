#!/usr/bin/env python3
"""Измерение скорости загрузки по десяти последовательным HTTP-запросам."""

from __future__ import annotations

import argparse
import ssl
import sys
import time
from dataclasses import dataclass
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

REQUEST_COUNT = 10
CHUNK_SIZE = 64 * 1024  # Читаем ответ частями, чтобы ограничить использование памяти.
DEFAULT_TIMEOUT = 30.0


@dataclass(frozen=True)
class RequestResult:
	"""Результаты измерений для одного завершённого HTTP-запроса."""

	number: int
	status_code: int
	downloaded_bytes: int
	elapsed_seconds: float


def validate_url(value: str) -> str:
	"""Проверяет URL и возвращает его, если это корректный HTTP(S)-адрес."""
	try:
		parsed = urlsplit(value)
		# Обращение к .port также позволяет обнаружить некорректно заданный порт.
		_ = parsed.port
	except ValueError as exc:
		raise ValueError(f"Некорректный URL: {exc}") from exc

	if parsed.scheme not in {"http", "https"} or not parsed.netloc:
		raise ValueError("Укажите полный URL, начинающийся с http:// или https://")

	return value


def positive_float(value: str) -> float:
	"""Преобразует таймаут в число и проверяет, что он больше нуля."""
	try:
		number = float(value)
	except ValueError as exc:
		raise argparse.ArgumentTypeError("Таймаут должен быть числом") from exc

	if number <= 0:
		raise argparse.ArgumentTypeError("Таймаут должен быть больше нуля")

	return number


def download_once(url: str, number: int, timeout: float) -> RequestResult:
	"""Полностью скачивает один HTTP-ответ и возвращает результаты измерений."""
	request = Request(
		url,
		headers={
			"User-Agent": "ETOLEGKO-Speed-Test/1.0",
			"Accept-Encoding": "identity",
			"Cache-Control": "no-cache",
			"Pragma": "no-cache",
		},
		method="GET",
	)

	started_at = time.perf_counter()
	downloaded_bytes = 0

	try:
		with urlopen(request, timeout=timeout) as response:
			status_code = response.status
			if not 200 <= status_code < 300:
				raise RuntimeError(
					f"Запрос №{number}: неожиданный HTTP-статус {status_code}"
				)

			# Читаем ответ до конца: запрос считается завершённым только после
			# получения всего тела ответа.
			while chunk := response.read(CHUNK_SIZE):
				downloaded_bytes += len(chunk)
	except HTTPError as exc:
		raise RuntimeError(
			f"Запрос №{number} завершился ошибкой: HTTP {exc.code} ({exc.reason})"
		) from exc
	except URLError as exc:
		# Ошибку проверки TLS-сертификата объясняем отдельно: это проблема
		# доверия к сертификату сервера или локальной цепочки сертификатов,
		# а не причина отключать проверку HTTPS.
		if isinstance(exc.reason, ssl.SSLCertVerificationError):
			hostname = urlsplit(url).hostname or url
			raise RuntimeError(
				f"Не удалось проверить TLS-сертификат сервера '{hostname}'. "
				"Проверьте сертификат сайта, системное хранилище сертификатов "
				"или настройки прокси. Проверка сертификатов не отключается."
			) from exc

		raise RuntimeError(f"Ошибка соединения при запросе №{number}: {exc.reason}") from exc
	except TimeoutError as exc:
		raise RuntimeError(
			f"Запрос №{number}: превышено время ожидания ({timeout:g} с)"
		) from exc

	elapsed_seconds = time.perf_counter() - started_at
	return RequestResult(
		number=number,
		status_code=status_code,
		downloaded_bytes=downloaded_bytes,
		elapsed_seconds=elapsed_seconds,
	)


def run_speed_test(
	url: str, timeout: float = DEFAULT_TIMEOUT
) -> tuple[list[RequestResult], float]:
	"""Выполняет ровно REQUEST_COUNT загрузок последовательно."""
	results: list[RequestResult] = []
	total_started_at = time.perf_counter()

	for number in range(1, REQUEST_COUNT + 1):
		results.append(download_once(url, number, timeout))

	total_elapsed_seconds = time.perf_counter() - total_started_at
	return results, total_elapsed_seconds


def format_megabytes(byte_count: int) -> str:
	"""Форматирует количество байтов в десятичных мегабайтах (1 МБ = 1 000 000 байт)."""
	return f"{byte_count / 1_000_000:.2f} МБ"


def main() -> int:
	parser = argparse.ArgumentParser(
		description=(
			f"Последовательно скачивает указанный URL {REQUEST_COUNT} раз, "
			"затем рассчитывает среднее время запроса и эффективную скорость загрузки."
		)
	)
	parser.add_argument(
		"url",
		help="Адрес файла или ресурса для скачивания (HTTP или HTTPS)",
	)
	parser.add_argument(
		"--timeout",
		type=positive_float,
		default=DEFAULT_TIMEOUT,
		help=f"Таймаут каждого запроса в секундах (по умолчанию: {DEFAULT_TIMEOUT:g})",
	)
	args = parser.parse_args()

	try:
		url = validate_url(args.url)
		results, total_elapsed_seconds = run_speed_test(url, args.timeout)
	except (ValueError, RuntimeError, OSError) as exc:
		print(f"Ошибка: {exc}", file=sys.stderr)
		return 1
	except KeyboardInterrupt:
		print("\nВыполнение прервано.", file=sys.stderr)
		return 130

	total_bytes = sum(result.downloaded_bytes for result in results)
	average_request_seconds = sum(
		result.elapsed_seconds for result in results
	) / len(results)
	download_speed_mb_s = (
		total_bytes / 1_000_000 / total_elapsed_seconds
		if total_elapsed_seconds > 0
		else 0.0
	)

	print("Результаты запросов:")
	for result in results:
		print(
			f"  {result.number:02d}/{REQUEST_COUNT}: "
			f"HTTP {result.status_code}, "
			f"{format_megabytes(result.downloaded_bytes)}, "
			f"{result.elapsed_seconds:.3f} с"
		)

	print("\nИтоги:")
	print(f"  Количество запросов:   {len(results)} (последовательно)")
	print(
		f"  Всего скачано:         {format_megabytes(total_bytes)} "
		f"({total_bytes:,} байт)"
	)
	print(f"  Среднее время запроса: {average_request_seconds:.3f} с")
	print(f"  Общее время:           {total_elapsed_seconds:.3f} с")
	print(f"  Скорость загрузки:     {download_speed_mb_s:.2f} МБ/с")

	return 0


if __name__ == "__main__":
	raise SystemExit(main())
