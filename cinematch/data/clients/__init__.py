"""Клиенты внешних систем модуля данных: TMDB и Open-Meteo — data/150.

Только этот пакет знает о внешних API (data/150 §1, п. 2): клиенты не
обращаются к базе, а отдают нормализованные значения; запись выполняют
репозитории. Другие модули к TMDB и Open-Meteo не обращаются.
"""

from __future__ import annotations

from . import tmdb, weather

__all__ = ["tmdb", "weather"]
