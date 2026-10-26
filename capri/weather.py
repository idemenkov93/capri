"""Погода словами, без браузера: Open-Meteo (бесплатно, без ключа и регистрации)."""
from __future__ import annotations

import re

import httpx

GEO = "https://geocoding-api.open-meteo.com/v1/search"
API = "https://api.open-meteo.com/v1/forecast"

# коды погоды WMO → текст
CODES = {
    0: ("ясно", "clear", "klar"), 1: ("в основном ясно", "mostly clear", "überwiegend klar"),
    2: ("переменная облачность", "partly cloudy", "teilweise bewölkt"), 3: ("пасмурно", "overcast", "bedeckt"),
    45: ("туман", "fog", "Nebel"), 48: ("изморозь и туман", "freezing fog", "Raureif-Nebel"),
    51: ("морось", "drizzle", "Nieselregen"), 53: ("морось", "drizzle", "Nieselregen"), 55: ("сильная морось", "heavy drizzle", "starker Nieselregen"),
    56: ("ледяная морось", "freezing drizzle", "gefrierender Niesel"), 57: ("ледяная морось", "freezing drizzle", "gefrierender Niesel"),
    61: ("небольшой дождь", "light rain", "leichter Regen"), 63: ("дождь", "rain", "Regen"), 65: ("сильный дождь", "heavy rain", "starker Regen"),
    66: ("ледяной дождь", "freezing rain", "gefrierender Regen"), 67: ("ледяной дождь", "freezing rain", "gefrierender Regen"),
    71: ("небольшой снег", "light snow", "leichter Schnee"), 73: ("снег", "snow", "Schnee"), 75: ("сильный снег", "heavy snow", "starker Schnee"),
    77: ("снежная крупа", "snow grains", "Schneegriesel"), 80: ("ливень", "showers", "Schauer"), 81: ("ливни", "showers", "Schauer"),
    82: ("сильные ливни", "violent showers", "heftige Schauer"), 85: ("снегопад", "snow showers", "Schneeschauer"),
    86: ("сильный снегопад", "heavy snow showers", "starke Schneeschauer"), 95: ("гроза", "thunderstorm", "Gewitter"),
    96: ("гроза с градом", "thunderstorm with hail", "Gewitter mit Hagel"), 99: ("гроза с градом", "thunderstorm with hail", "Gewitter mit Hagel"),
}

RE_WEATHER = re.compile(
    r"\b(погод\w*|прогноз погоды|сколько градусов|температур\w* (на|за) улиц\w*|будет ли (дождь|снег)|"
    r"(холодно|жарко|тепло) (ли )?(сегодня|завтра|на улице)|нужен ли зонт|брать ли зонт|"
    r"weather|forecast|wetter|regnet es|will it rain|umbrella)\b", re.I)
RE_CITY = re.compile(r"\b(?:в|во|in|für|for)\s+([A-ZА-ЯЁÄÖÜ][\w\-ёäöüß]+(?:[\s-][A-ZА-ЯЁÄÖÜ][\w\-ёäöüß]+)?)")
RE_TOMORROW = re.compile(r"\bзавтра|tomorrow|morgen\b", re.I)
RE_AFTER = re.compile(r"послезавтра|day after tomorrow|übermorgen", re.I)


def is_weather_question(text: str) -> bool:
    return bool(RE_WEATHER.search(text))


def parse(text: str, default_city: str) -> tuple[str, int]:
    """(город, день: 0 сегодня, 1 завтра, 2 послезавтра)."""
    day = 2 if RE_AFTER.search(text) else 1 if RE_TOMORROW.search(text) else 0
    m = RE_CITY.search(text)
    city = m.group(1) if m else default_city
    # «в Мюнхене» → «Мюнхен»: геокодер ищет по началу названия, падеж ему мешает
    if re.search(r"[а-яё]", city, re.I):
        city = re.sub(r"(е|у|ом|ах)$", "", city) if len(city) > 5 else city
    return city, day


def _geocode(city: str, lang: str, client) -> dict | None:
    r = client.get(GEO, params={"name": city, "count": 1, "language": lang if lang in ("ru", "de", "en") else "en"})
    res = r.json().get("results") or []
    return res[0] if res else None


def forecast(text: str, default_city: str, lang: str, client=None) -> str:
    city, day = parse(text, default_city)
    own = client is None
    client = client or httpx.Client(timeout=10)
    try:
        place = _geocode(city, lang, client) or (_geocode(default_city, lang, client) if city != default_city else None)
        if not place:
            return {"ru": f"Не нашла город «{city}».", "de": f"Stadt „{city}“ nicht gefunden.", "en": f"Couldn't find “{city}”."}.get(lang, f"Couldn't find {city}")
        data = client.get(API, params={
            "latitude": place["latitude"], "longitude": place["longitude"], "timezone": "auto", "forecast_days": 3,
            "current": "temperature_2m,apparent_temperature,weather_code,wind_speed_10m,precipitation",
            "daily": "temperature_2m_max,temperature_2m_min,precipitation_probability_max,weather_code",
        }).json()
    finally:
        if own:
            client.close()
    return describe(data, place.get("name", city), day, lang)


def describe(data: dict, name: str, day: int, lang: str) -> str:
    i = {"ru": 0, "en": 1, "de": 2}.get(lang, 1)
    d = data["daily"]
    tmax, tmin = round(d["temperature_2m_max"][day]), round(d["temperature_2m_min"][day])
    rain = d.get("precipitation_probability_max", [None] * 3)[day]
    sky = CODES.get(d["weather_code"][day], ("", "", ""))[i]
    if day == 0 and "current" in data:
        c = data["current"]
        now, feels = round(c["temperature_2m"]), round(c["apparent_temperature"])
        sky_now = CODES.get(c["weather_code"], ("", "", ""))[i]
        if lang == "ru":
            s = f"В городе {name} сейчас {now}°, ощущается как {feels}°, {sky_now}. Сегодня от {tmin}° до {tmax}°"
        elif lang == "de":
            s = f"In {name} sind es gerade {now} Grad, gefühlt {feels}, {sky_now}. Heute {tmin} bis {tmax} Grad"
        else:
            s = f"In {name} it's {now}° right now, feels like {feels}°, {sky_now}. Today {tmin}° to {tmax}°"
    else:
        when = [("сегодня", "today", "heute"), ("завтра", "tomorrow", "morgen"), ("послезавтра", "the day after tomorrow", "übermorgen")][day][i]
        if lang == "ru":
            s = f"{when.capitalize()} в городе {name} от {tmin}° до {tmax}°, {sky}"
        elif lang == "de":
            s = f"{when.capitalize()} in {name} {tmin} bis {tmax} Grad, {sky}"
        else:
            s = f"{when.capitalize()} in {name}: {tmin}° to {tmax}°, {sky}"
    if rain is not None:
        s += {"ru": f", вероятность осадков {rain}%.", "de": f", Regenwahrscheinlichkeit {rain} %.",
              "en": f", {rain}% chance of rain."}.get(lang, ".")
        if rain >= 50:
            s += {"ru": " Зонт возьми.", "de": " Nimm einen Schirm mit.", "en": " Take an umbrella."}.get(lang, "")
    else:
        s += "."
    return s
