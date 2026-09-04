import json
import os
import logging
import asyncio
import wave
import contextlib
from typing import Any
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
import uvicorn
from contextlib import asynccontextmanager
import httpx
from core.vision import analyze_screen_async
from core.brain import ask_brain, ask_brain_proactive, memory
from core.time_engine import TimeEngine
from core.gcal_helper import GoogleCalendarManager
from core.vision import capture_screen_image, calculate_image_mse, detect_screen_changes_async, analyze_screen_async
import time
from logging.handlers import RotatingFileHandler
import glob
from core.config_manager import config_manager
from core.autodl_tts import AutoDLTTSConnection
from core.tts_manager import TTSManager  # 引入新的管理器

last_vision_trigger_time = time.time()
# -------------------------------------------------------------------
# 1. 基礎設定與環境準備
# -------------------------------------------------------------------
log_dir = os.path.join(os.path.dirname(__file__), "logs")
os.makedirs(log_dir, exist_ok=True) 

log_file_path = os.path.join(log_dir, "system.log")

log_formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")

console_handler = logging.StreamHandler()
console_handler.setFormatter(log_formatter)

file_handler = RotatingFileHandler(
    log_file_path, maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8"
)
file_handler.setFormatter(log_formatter)

logging.basicConfig(
    level=logging.INFO,
    handlers=[console_handler, file_handler]
)
logger = logging.getLogger("PetMiddleware")

logger.info("=========================================")
logger.info(f"🚀 系統啟動：日誌系統已初始化 (日誌將寫入: {log_file_path})")

AUDIO_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "../assets/Murasame/sounds"))
os.makedirs(AUDIO_DIR, exist_ok=True)

# -------------------------------------------------------------------
# 2. 音檔長度計算工具
# -------------------------------------------------------------------
def get_audio_duration(file_path: str) -> float:
    if not os.path.exists(file_path) or not file_path.lower().endswith('.wav'):
        return 2.0 
    try:
        with contextlib.closing(wave.open(file_path, 'r')) as f:
            frames = f.getnframes()
            rate = f.getframerate()
            duration = frames / float(rate)
            return duration
    except Exception as e:
        logger.error(f"無法讀取音檔長度: {e}")
        return 2.0

autodl_conn = AutoDLTTSConnection()
tts_manager = TTSManager(autodl_conn, AUDIO_DIR)
time_engine = None

# -------------------------------------------------------------------
# 3. 主動推播回呼函數 (排程時間到時觸發)
# -------------------------------------------------------------------
async def proactive_trigger_callback(secret_prompt: str):
    logger.info("⏰ 系統時間觸發，正在向大腦請求主動發言...")
    llm_result = await ask_brain_proactive(secret_prompt)
    
    # 🌟 檢查大腦是否行使了拒絕權 (action_code 為 0 或 messages 為空)
    if llm_result.get("action_code") == 0 or not llm_result.get("messages"):
        logger.info("🧠 [主動發言] 大腦選擇保持安靜（拒絕發言）。")
        return "" # 👈 回傳空字串代表沒說話

    if llm_result.get("action_code") == 1 and manager.active_connections:
        messages = llm_result.get("messages", [])
        
        full_spoken_text = "" # 👈 新增：用來把分段的句子接起來
        
        for msg in messages:
            full_spoken_text += msg.get("reply_zh", "") # 👈 收集台詞
            text_to_speak = msg.get("reply_jp", "")
            local_mp3_path, audio_url = "", ""
            
            if text_to_speak:
                local_mp3_path, audio_url = await generate_tts(text_to_speak, msg.get("emotion", 5))

            payload: dict[str, Any]= {
                "reply_zh": msg.get("reply_zh", ""),
                "reply_jp": text_to_speak,
                "emotion": msg.get("emotion", 5),
                "playMotion": msg.get("playMotion", False),
                "motion": msg.get("motion", ""),
                "audio_url": audio_url
            }
            
            for ws in manager.active_connections:
                try:
                    await ws.send_json(payload)
                except Exception as e:
                    logger.error(f"主動發送 WebSocket 失敗: {e}")
            
            logger.info(f"📤 [主動推播完成]: {payload['reply_zh']}")
            sleep_time = get_audio_duration(local_mp3_path) + 0.5
            await asyncio.sleep(sleep_time)
            return full_spoken_text # 👈 將真正說出口的話回傳給時間引擎
        
    return ""
async def screen_monitor_loop():
    """背景視覺監控迴圈"""
    global last_vision_trigger_time
    logger.info("👁️ 桌面視覺背景監控已啟動...")
    previous_scene_json = "null"
    last_image = None
    
    while True:
        try:
            await asyncio.sleep(15)

            # --- 將設定移到迴圈內，每次甦醒都動態獲取最新值 ---
            MSE_THRESHOLD = config_manager.get("vision_mse_threshold", 500.0)
            v_model = config_manager.get("sub_model", "gpt-4o-mini")
            cooldown_seconds = config_manager.get("vision_cooldown_seconds", 600)
            
            # 即時檢查最新的勿擾模式
            if config_manager.get("do_not_disturb", False):
                continue
            
            if not manager.active_connections:
                continue

            current_time = time.time()
            if current_time - last_vision_trigger_time < cooldown_seconds:
                continue

            current_image = await asyncio.to_thread(capture_screen_image)
            if not current_image:
                continue

            if last_image is not None:
                mse_value = await asyncio.to_thread(calculate_image_mse, last_image, current_image)
                if mse_value < MSE_THRESHOLD:
                    continue

            logger.info("📡 畫面出現實體變動，正在呼叫 OpenAI 進行深度解析...")

            result: dict[str, Any] = await detect_screen_changes_async(
                model_name=v_model,
                previous_scene_json=previous_scene_json,
                current_img=current_image
            )

            if "error" not in result:
                last_image = current_image 
                previous_scene_json = json.dumps(result, ensure_ascii=False)

            if result.get("significant_change") is True:
                change_summary = result.get("change_summary", "")
                activity = result.get("activity", "")
                logger.info(f"✨ OpenAI 判定為顯著變化: {change_summary}")
                
                secret_prompt = (
                    f"【系統提示】你正在螢幕邊緣觀察主人。主人的畫面剛剛發生了明顯的變化：\n"
                    f"現在的畫面狀態：{activity}\n"
                    f"變化細節：{change_summary}\n\n"
                    f"請以叢雨的身份，對此變化發表 1~2 句簡短的評論、吐槽或關心。"
                )
                
                await proactive_trigger_callback(secret_prompt)
                
                last_vision_trigger_time = time.time()
                logger.info(f"⏳ 進入視覺冷卻時間，{cooldown_seconds} 秒內不再自動觀察螢幕。")

        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"視覺監控迴圈發生錯誤: {e}")

# -------------------------------------------------------------------
# 4. FastAPI 生命週期：掛載服務
# -------------------------------------------------------------------
monitor_task = None  
@asynccontextmanager
async def lifespan(app: FastAPI):
    global time_engine , monitor_task
    # ==========================================
    # 1. 啟動時：清除上次殘留的 TTS 音檔快取
    # ==========================================
    logger.info("🧹 正在清除上次殘留的語音快取...")
    try:
        # 尋找 AUDIO_DIR 底下所有以 response 開頭並以 .wav 結尾的檔案
        cache_pattern = os.path.join(AUDIO_DIR, "response*.wav")
        for f in glob.glob(cache_pattern):
            os.remove(f)
        logger.info("✅ 語音快取清除完畢！")
    except Exception as e:
        logger.error(f"清除語音快取失敗: {e}")
        
    # ==========================================
    # 2. 啟動時：初始化所有子系統
    # ==========================================
    logger.info("🚀 正在啟動 TTS 子系統...")
    await tts_manager.start(config_manager)
            
    gcal_manager = None
    if config_manager.get("enable_google_calendar"):
        try:
            gcal_manager = GoogleCalendarManager(config_manager.get("gcal_credentials_path", "credentials.json"))
            logger.info("✅ Google 日曆掛載成功！")
        except Exception as e:
            logger.warning(f"⚠️ Google 日曆掛載失敗: {e}")

    time_engine = TimeEngine(
        collection=memory.collection, 
        gcal_manager=gcal_manager,
        brain_api_callback=proactive_trigger_callback
    )
    logger.info("✅ 時間模組 (TimeEngine) 已啟動！")

    monitor_task = asyncio.create_task(screen_monitor_loop())
    logger.info("✅ 視覺監控背景任務已掛載！")
    
    # ==========================================
    # 🚀 分水嶺：伺服器準備好，開始接受前端請求
    # ==========================================
    yield 
    
    # ==========================================
    # 3. 關閉時：執行清理工作
    # ==========================================
    logger.info("🛑 關閉 TTS 服務...")
    tts_manager.stop()
    
    logger.info("🛑 關閉 AutoDL 連線...")
    autodl_conn.stop()

app = FastAPI(title="Murasame AI Middleware", lifespan=lifespan)
app.mount("/audio", StaticFiles(directory=AUDIO_DIR), name="audio")
@app.get("/reload")
async def reload_settings():
    logger.info("🔄 收到前端設定更改，正在重新載入設定檔 (熱修改)...")
    config_manager.load()
    if time_engine is not None:
        time_engine.update_random_event_interval()
    try:
        tts_manager.stop() 
        success = await tts_manager.start(config_manager) 
        
        # 【修改這裡】加入 true/false 的判斷
        if success:
            logger.info("✅ TTS 熱修改切換成功！")
            return {"status": "success", "message": "設定已熱修改生效"}
        else:
            logger.error("❌ TTS 熱修改失敗，請檢查終端機報錯。")
            return {"status": "error", "message": "TTS 啟動失敗，請檢查終端機"}
            
    except Exception as e:
        logger.error(f"⚠️ 熱修改時 TTS 切換失敗: {e}")
        return {"status": "error", "message": f"設定已生效，但 TTS 啟動發生異常: {e}"}
  

# (原本的 /shutdown 可以保留，作為純粹的關閉程式功能)
@app.get("/shutdown")
def shutdown_server():
    logger.info("🛑 收到前端設定更改，正在關閉背景服務...")
    
    try:
        autodl_conn.stop()
        logger.info("🛑 AutoDL 連線已強制中斷")
    except Exception as e:
        logger.error(f"關閉 AutoDL 時發生錯誤: {e}")
        
    logger.info("🛑 執行強制退出以觸發 launch.js 重啟機制...")
    os._exit(0)

# -------------------------------------------------------------------
# 5. TTS 生成功能
# -------------------------------------------------------------------
async def generate_tts(text_jp: str, emotion_code: int = 5) -> tuple[str, str]:
    return await tts_manager.generate(text_jp, emotion_code)

# -------------------------------------------------------------------
# 6. WebSocket 連線管理員
# -------------------------------------------------------------------
class ConnectionManager:
    def __init__(self):
        self.active_connections: list[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)
        logger.info(f"前端已連線！目前連線數：{len(self.active_connections)}")

    def disconnect(self, websocket: WebSocket):
        self.active_connections.remove(websocket)
        logger.info(f"前端已斷線！目前連線數：{len(self.active_connections)}")

manager = ConnectionManager()

# -------------------------------------------------------------------
# 6.5 天氣模組 (Weather Module) - 3天預報 + 風速 + 嚴格型別註解版
# -------------------------------------------------------------------
async def get_weather_async(location: str = "Taipei") -> str:
    """透過免費 API 取得當前天氣、3天預報、降雨機率與風速資訊"""
    try:
        api_url = f"https://wttr.in/{location}?format=j1&lang=zh-tw"
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(api_url)
            if response.status_code == 200:
                data: dict[str, Any] = response.json()
                
                # 1. 抓取當下即時氣候與風速
                current: dict[str, Any] = data['current_condition'][0]
                desc_list: list[dict[str, Any]] = current.get(
                    'lang_zh-tw', 
                    current.get('lang_zh', current.get('weatherDesc', [{'value': '未知'}]))
                )
                current_desc: str = str(desc_list[0].get('value', '未知'))
                current_temp: str = str(current.get('temp_C', '未知'))
                current_wind_speed: str = str(current.get('windspeedKmph', '0'))
                current_wind_dir: str = str(current.get('winddir16Point', ''))
                
                # 內部輔助函數：補全型別標註，解決 Pylance 警告
                def calculate_day_stats(hourly_list: list[dict[str, Any]]) -> tuple[int, int]:
                    max_rain: int = max(int(h.get('chanceofrain', '0')) for h in hourly_list)
                    max_wind: int = max(int(h.get('windspeedKmph', '0')) for h in hourly_list)
                    return max_rain, max_wind

                # 2. 抓取未來 3 天資料 (wttr.in 預設回傳 3 天)
                weather_days: list[dict[str, Any]] = data.get('weather', [])[:3]
                day_labels: list[str] = ["今天", "明天", "後天"]
                forecast_lines: list[str] = []

                for idx, day_data in enumerate(weather_days):
                    label: str = day_labels[idx] if idx < len(day_labels) else f"第 {idx + 1} 天"
                    date_str: str = str(day_data.get('date', ''))
                    max_temp: str = str(day_data.get('maxtempC', ''))
                    min_temp: str = str(day_data.get('mintempC', ''))
                    
                    hourly_data: list[dict[str, Any]] = day_data.get('hourly', [])
                    max_rain, max_wind = calculate_day_stats(hourly_data)
                    
                    # 強風提醒邏輯 (陣風/風速大於 30 km/h 時標記)
                    wind_warning: str = " ⚠️強風" if max_wind >= 30 else ""

                    forecast_lines.append(
                        f"・{label} ({date_str})：氣溫 {min_temp}°C~{max_temp}°C | "
                        f"最高降雨 {max_rain}% | 最大風速 {max_wind} km/h{wind_warning}"
                    )

                # 月相資訊
                moon_phase: str = "未知"
                if weather_days and 'astronomy' in weather_days[0]:
                    moon_phase = str(weather_days[0]['astronomy'][0].get('moon_phase', '未知'))

                # 組合最終給 LLM 的 context 字串
                weather_summary: str = (
                    f"目前地點：{location}\n"
                    f"當下狀態：{current_desc}，氣溫 {current_temp}°C，風向 {current_wind_dir} (風速 {current_wind_speed} km/h)。\n"
                    f"今晚月相：{moon_phase}\n"
                    f"【未來三天預報】\n" + "\n".join(forecast_lines)
                )
                return weather_summary
                
            return f"無法取得 {location} 的天氣資訊 (HTTP {response.status_code})。"
    except Exception as e:
        logger.error(f"天氣 API 連線失敗: {e}")
        return "天氣服務連線失敗，請稍後再試。"

# -------------------------------------------------------------------
# 7. WebSocket 路由
# -------------------------------------------------------------------
@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    global last_vision_trigger_time  
    await manager.connect(websocket)
    is_busy = False 
    
    try:
        while True:
            user_input: dict[str, Any] = await websocket.receive_json()
            logger.info(f"📥 收到前端請求: {user_input}")

            if user_input.get("type") == "system" and user_input.get("content") == "clear_memory":
                memory.clear()
                logger.info("🧹 已清空叢雨的短期記憶")
                await websocket.send_json({
                    "action_code": 1,
                    "reply_zh": "（記憶已清除）",
                    "reply_jp": "（記憶がクリアされました）",
                    "emotion": 5,
                    "playMotion": False,
                    "motion": "",
                    "audio_url": ""
                })
                continue 

            if is_busy:
                logger.warning("桌寵正在處理中，忽略此次連擊/請求")
                continue

            is_busy = True 
            
            try:
                if time_engine is None:
                    logger.error("系統尚未準備好 (TimeEngine 缺失)")
                    continue

                llm_result: dict[str, Any] = await ask_brain(user_input, time_engine=time_engine)

                if llm_result.get("action_code") == 2:
                    logger.info("🧠 大腦請求調用桌面視覺...")
                    last_vision_trigger_time = time.time()
                    
                    # 動態獲取模型
                    v_model = config_manager.get("sub_model", "gpt-4o-mini")
                    screen_description: str = await analyze_screen_async(model_name=v_model)

                    llm_result = await ask_brain(
                        user_input, 
                        time_engine=time_engine, 
                        screen_description=screen_description
                    )

                if llm_result.get("action_code") == 3:
                    logger.info("🌤️ 大腦請求調用天氣資訊...")
                    
                    # 動態獲取天氣位置，加入預設城市防錯
                    target_location = config_manager.get("weather_location", "Jiali District, Tainan City, Taiwan")
                    weather_info: str = await get_weather_async(target_location)
                    logger.info(f"🌤️ 取得 {target_location} 天氣結果: {weather_info}")

                    llm_result = await ask_brain(
                        user_input, 
                        time_engine=time_engine, 
                        weather_info=weather_info  
                    )

                if llm_result.get("action_code") == 1:
                    messages = llm_result.get("messages", [])
                    for msg in messages:
                        text_to_speak = msg.get("reply_jp", "")
                        local_mp3_path, audio_url = "", ""
                        
                        if text_to_speak:
                            local_mp3_path, audio_url = await generate_tts(text_to_speak, msg.get("emotion", 5))

                        payload: dict[str, Any] = {
                            "reply_zh": msg.get("reply_zh", ""),
                            "reply_jp": text_to_speak,
                            "emotion": msg.get("emotion", 5),
                            "playMotion": msg.get("playMotion", False),
                            "motion": msg.get("motion", ""),
                            "audio_url": audio_url
                        }
                        
                        await websocket.send_json(payload)
                        logger.info(f"📤 已回傳前端片段: {payload['reply_zh']}")
                        
                        sleep_time: float = get_audio_duration(local_mp3_path) + 0.5
                        await asyncio.sleep(sleep_time)

            except Exception as e:
                logger.error(f"處理流程發生錯誤: {e}")
            finally:
                is_busy = False 

    except WebSocketDisconnect:
        manager.disconnect(websocket)
    except Exception as e:
        logger.error(f"WebSocket 發生未預期錯誤: {e}")

# -------------------------------------------------------------------
# 8. 啟動入口
# -------------------------------------------------------------------
if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=False)