import httpx
import logging
from typing import Any
from core.config_manager import config_manager

logger = logging.getLogger("WeatherModule")

async def get_current_location_async() -> str:
    """自動判斷是否啟用 IP 定位，否則讀取 config 的設定"""
    auto_loc = config_manager.get("auto_location", True)
    default_loc = config_manager.get("weather_location", "Taipei")
    
    if not auto_loc:
        return default_loc

    try:
        # 使用免費 IP 定位 API (免 Key，全球通用)
        async with httpx.AsyncClient(timeout=3.0) as client:
            resp = await client.get("http://ip-api.com/json/?fields=status,city")
            if resp.status_code == 200:
                data = resp.json()
                if data.get("status") == "success" and data.get("city"):
                    return str(data["city"])
    except Exception as e:
        logger.warning(f"⚠️ IP 自動定位失敗，退回預設地點 {default_loc}: {e}")
        
    return default_loc

async def get_weather_async() -> str:
    """透過免費 API 取得超詳細當前天氣與未來 3 天預報，供大腦自由挑選重點與回答未來趨勢"""
    location = await get_current_location_async()
    
    try:
        api_url = f"https://wttr.in/{location}?format=j1&lang=zh-tw"
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(api_url)
            if response.status_code == 200:
                data: dict[str, Any] = response.json()
                
                # 1. 抓取當下即時資訊 (包含體感、濕度、UV、能見度)
                current: dict[str, Any] = data.get('current_condition', [{}])[0]
                desc_list = current.get('lang_zh-tw', current.get('lang_zh', [{'value': '未知'}]))
                current_desc: str = str(desc_list[0].get('value', '未知')) if desc_list else '未知'
                
                temp: str = str(current.get('temp_C', '未知'))
                feels_like: str = str(current.get('FeelsLikeC', '未知'))
                humidity: str = str(current.get('humidity', '未知'))
                uv_index: str = str(current.get('uvIndex', '未知'))
                visibility: str = str(current.get('visibility', '未知'))
                wind_speed: str = str(current.get('windspeedKmph', '0'))
                wind_dir: str = str(current.get('winddir16Point', ''))
                
                # 2. 抓取未來 3 天預報 (今天、明天、後天)
                weather_days: list[dict[str, Any]] = data.get('weather', [])[:3]
                day_labels: list[str] = ["今天", "明天", "後天"]
                forecast_lines: list[str] = []

                def calculate_day_stats(hourly_list: list[dict[str, Any]]) -> tuple[int, int, int]:
                    max_rain = max((int(h.get('chanceofrain', '0')) for h in hourly_list), default=0)
                    max_snow = max((int(h.get('chanceofsnow', '0')) for h in hourly_list), default=0)
                    max_wind = max((int(h.get('windspeedKmph', '0')) for h in hourly_list), default=0)
                    return max_rain, max_snow, max_wind

                for idx, day_data in enumerate(weather_days):
                    label: str = day_labels[idx] if idx < len(day_labels) else f"第 {idx + 1} 天"
                    date_str: str = str(day_data.get('date', ''))
                    max_temp: str = str(day_data.get('maxtempC', ''))
                    min_temp: str = str(day_data.get('mintempC', ''))
                    
                    hourly_data: list[dict[str, Any]] = day_data.get('hourly', [])
                    max_rain, max_snow, max_wind = calculate_day_stats(hourly_data)
                    
                    snow_str = f" | 降雪 {max_snow}%" if max_snow > 0 else ""
                    wind_warn = " ⚠️強風" if max_wind >= 30 else ""

                    forecast_lines.append(
                        f"・{label} ({date_str})：氣溫 {min_temp}°C~{max_temp}°C | "
                        f"降雨機率 {max_rain}%{snow_str} | 最大風速 {max_wind} km/h{wind_warn}"
                    )

                # 3. 月相資訊
                moon_phase: str = "未知"
                if weather_days and 'astronomy' in weather_days[0]:
                    moon_phase = str(weather_days[0]['astronomy'][0].get('moon_phase', '未知'))

                # 組合齊全的數據 Context 給大腦
                weather_summary = (
                    f"【地點】：{location}\n"
                    f"【當前實況】：{current_desc}，氣溫 {temp}°C (體感 {feels_like}°C)，濕度 {humidity}%，"
                    f"紫外線(UV) {uv_index}，能見度 {visibility}km，風向 {wind_dir} (風速 {wind_speed}km/h)，今晚月相：{moon_phase}\n"
                    f"【未來三天預報】\n" + "\n".join(forecast_lines)
                )
                return weather_summary
                
            return f"無法取得天氣資訊 (HTTP {response.status_code})。"
    except Exception as e:
        logger.error(f"天氣 API 連線失敗: {e}")
        return "天氣服務連線失敗，請稍後再試。"