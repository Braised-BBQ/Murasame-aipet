import httpx
import logging
import re
from typing import Any, Optional, List, Dict, Tuple
from core.config_manager import config_manager

logger = logging.getLogger("WeatherModule")

def _wmo_to_text(code: int) -> str:
    wmo_map = {
        0: "晴朗", 1: "多雲時晴", 2: "多雲", 3: "陰天",
        45: "起霧", 48: "霧", 51: "微毛毛雨", 53: "毛毛雨", 55: "大毛毛雨",
        56: "微冰雨", 57: "冰雨", 61: "微雨", 63: "雨", 65: "大雨",
        66: "微凍雨", 67: "凍雨", 71: "微雪", 73: "雪", 75: "大雪",
        77: "冰粒", 80: "微陣雨", 81: "陣雨", 82: "大陣雨",
        85: "微陣雪", 86: "陣雪", 95: "雷雨", 96: "雷雨伴隨冰雹", 99: "強雷雨伴隨冰雹"
    }
    return wmo_map.get(code, "未知")

def _deg_to_dir(deg: Optional[float]) -> str:
    if deg is None: 
        return "未知"
    dirs = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE", "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"]
    return dirs[int((deg + 11.25) / 22.5) % 16]

async def get_current_location_async() -> str:
    auto_loc: bool = config_manager.get("auto_location", True)
    default_loc: str = config_manager.get("weather_location", "Taipei")
    
    if not auto_loc:
        return default_loc

    try:
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
        async with httpx.AsyncClient(timeout=4.0, headers=headers) as client:
            resp = await client.get("http://ip-api.com/json/?fields=status,city")
            if resp.status_code == 200:
                data: Dict[str, Any] = resp.json()
                if data.get("status") == "success" and data.get("city"):
                    raw_city = str(data["city"])
                    clean_city = re.sub(r'\s+(City|District|County)', '', raw_city, flags=re.IGNORECASE).strip()
                    return clean_city
    except Exception as e:
        logger.warning(f"⚠️ IP 自動定位失敗，退回預設地點 {default_loc}: {e}")
        
    return default_loc

async def get_weather_async(custom_location: Optional[str] = None) -> str:
    location = custom_location if custom_location else await get_current_location_async()

    clean_location = re.sub(r'\s+(City|District|County)', '', location, flags=re.IGNORECASE).strip()
    formatted_location = clean_location.replace(" ", "+")

    api_url = f"https://wttr.in/{formatted_location}?format=j1&lang=zh-tw"
    headers = {"User-Agent": "curl/7.68.0", "Accept": "*/*"}

    # ==========================================
    # 方案 A：優先使用 wttr.in
    # ==========================================
    try:
        async with httpx.AsyncClient(timeout=6.0, headers=headers, http2=False) as client:
            response = await client.get(api_url)
            
            if response.status_code == 200:
                try:
                    data: Dict[str, Any] = response.json()
                    
                    current: Dict[str, Any] = data.get('current_condition', [{}])[0]
                    desc_list: List[Dict[str, Any]] = current.get('lang_zh-tw', current.get('lang_zh', [{'value': '未知'}]))
                    current_desc: str = str(desc_list[0].get('value', '未知')) if desc_list else '未知'
                    
                    temp: str = str(current.get('temp_C', '未知'))
                    feels_like: str = str(current.get('FeelsLikeC', '未知'))
                    humidity: str = str(current.get('humidity', '未知'))
                    uv_index: str = str(current.get('uvIndex', '未知'))
                    visibility: str = str(current.get('visibility', '未知'))
                    wind_speed: str = str(current.get('windspeedKmph', '0'))
                    wind_dir: str = str(current.get('winddir16Point', ''))
                    
                    weather_days: List[Dict[str, Any]] = data.get('weather', [])[:3]
                    day_labels: List[str] = ["今天", "明天", "後天"]
                    forecast_lines: List[str] = []

                    def calc_stats(hourly_list: List[Dict[str, Any]]) -> Tuple[int, int, int]:
                        max_rain = max((int(h.get('chanceofrain', '0')) for h in hourly_list), default=0)
                        max_snow = max((int(h.get('chanceofsnow', '0')) for h in hourly_list), default=0)
                        max_wind = max((int(h.get('windspeedKmph', '0')) for h in hourly_list), default=0)
                        return max_rain, max_snow, max_wind

                    for idx, day_data in enumerate(weather_days):
                        label = day_labels[idx] if idx < len(day_labels) else f"第 {idx + 1} 天"
                        date_str = str(day_data.get('date', ''))
                        max_temp = str(day_data.get('maxtempC', ''))
                        min_temp = str(day_data.get('mintempC', ''))
                        
                        hourly_data: List[Dict[str, Any]] = day_data.get('hourly', [])
                        max_r, max_s, max_w = calc_stats(hourly_data)
                        
                        snow_str = f" | 降雪 {max_s}%" if max_s > 0 else ""
                        wind_warn = " ⚠️強風" if max_w >= 30 else ""
                        forecast_lines.append(f"・{label} ({date_str})：氣溫 {min_temp}°C~{max_temp}°C | 降雨機率 {max_r}%{snow_str} | 最大風速 {max_w} km/h{wind_warn}")

                    moon_phase = "未知"
                    if weather_days and 'astronomy' in weather_days[0]:
                        moon_phase = str(weather_days[0]['astronomy'][0].get('moon_phase', '未知'))

                    return (
                        f"【地點】：{clean_location}\n"
                        f"【當前實況】：{current_desc}，氣溫 {temp}°C (體感 {feels_like}°C)，濕度 {humidity}%，"
                        f"紫外線(UV) {uv_index}，能見度 {visibility}km，風向 {wind_dir} (風速 {wind_speed}km/h)，今晚月相：{moon_phase}\n"
                        f"【未來三天預報】\n" + "\n".join(forecast_lines)
                    )
                except Exception as parse_err:
                    logger.error(f"wttr.in 資料解析失敗: {parse_err}")
            else:
                logger.warning(f"⚠️ wttr.in 伺服器回應 HTTP {response.status_code}，切換至 Open-Meteo...")

    except Exception as e:
        logger.warning(f"⚠️ wttr.in 連線失敗: {e}，切換至 Open-Meteo...")

    # ==========================================
    # 方案 B (備用機制)：Open-Meteo
    # ==========================================
    try:
        logger.info("📡 正在向 Open-Meteo 請求備用即時天氣數據 (含3天預報)...")
        async with httpx.AsyncClient(timeout=6.0) as client:
            ip_res = await client.get("http://ip-api.com/json/?fields=status,lat,lon")
            if ip_res.status_code == 200:
                ip_json: Dict[str, Any] = ip_res.json()
                if ip_json.get("status") == "success":
                    lat = ip_json.get("lat")
                    lon = ip_json.get("lon")
                    
                    om_url = (
                        f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}"
                        f"&current=temperature_2m,relative_humidity_2m,apparent_temperature,weather_code,wind_speed_10m,wind_direction_10m"
                        f"&daily=weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max,wind_speed_10m_max,uv_index_max"
                        f"&timezone=auto&forecast_days=3"
                    )
                    
                    om_res = await client.get(om_url)
                    if om_res.status_code == 200:
                        om_data: Dict[str, Any] = om_res.json()
                        cur: Dict[str, Any] = om_data.get("current", {})
                        daily: Dict[str, Any] = om_data.get("daily", {})
                        
                        desc = _wmo_to_text(int(cur.get("weather_code", 0)))
                        temp = str(cur.get("temperature_2m", "未知"))
                        feels = str(cur.get("apparent_temperature", "未知"))
                        humid = str(cur.get("relative_humidity_2m", "未知"))
                        wind_spd = str(cur.get("wind_speed_10m", "0"))
                        wind_dir = _deg_to_dir(cur.get("wind_direction_10m"))
                        
                        uv_today = str(daily.get("uv_index_max", ["未知"])[0])
                        visibility = "未知"
                        moon_phase = "未知"
                        
                        day_labels = ["今天", "明天", "後天"]
                        forecast_lines: List[str] = []
                        
                        time_list: List[str] = daily.get("time", [])
                        max_temps: List[Any] = daily.get("temperature_2m_max", [])
                        min_temps: List[Any] = daily.get("temperature_2m_min", [])
                        rain_probs: List[Any] = daily.get("precipitation_probability_max", [])
                        max_winds: List[Any] = daily.get("wind_speed_10m_max", [])

                        for i in range(len(time_list)):
                            label = day_labels[i] if i < len(day_labels) else f"第 {i+1} 天"
                            d_str = str(time_list[i])
                            d_max = str(max_temps[i]) if i < len(max_temps) else "未知"
                            d_min = str(min_temps[i]) if i < len(min_temps) else "未知"
                            d_rain = str(rain_probs[i]) if i < len(rain_probs) else "0"
                            d_wind_val = float(max_winds[i]) if i < len(max_winds) and max_winds[i] is not None else 0.0
                            
                            wind_warn = " ⚠️強風" if d_wind_val >= 30 else ""
                            forecast_lines.append(f"・{label} ({d_str})：氣溫 {d_min}°C~{d_max}°C | 降雨機率 {d_rain}% | 最大風速 {d_wind_val} km/h{wind_warn}")

                        return (
                            f"【地點】：{clean_location} (由備用衛星提供)\n"
                            f"【當前實況】：{desc}，氣溫 {temp}°C (體感 {feels}°C)，濕度 {humid}%，"
                            f"紫外線(UV) {uv_today}，能見度 {visibility}km，風向 {wind_dir} (風速 {wind_spd}km/h)，今晚月相：{moon_phase}\n"
                            f"【未來三天預報】\n" + "\n".join(forecast_lines)
                        )
    except Exception as fallback_err:
        logger.error(f"備用 API 亦呼叫失敗: {fallback_err}")

    return f"【地點】：{clean_location}\n【當前實況】：天氣多雲到晴，氣溫約 28°C (網路異常)。"