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
from core.autodl_tts import AutoDLTTSConnection, AutoDLConnectionError
from core.brain import ask_brain, ask_brain_proactive, memory
from core.time_engine import TimeEngine
from core.gcal_helper import GoogleCalendarManager
from core.vision import capture_screen_image, calculate_image_mse, detect_screen_changes_async, analyze_screen_async
import time
from logging.handlers import RotatingFileHandler
import glob

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

config_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "config.json"))
with open(config_path, "r", encoding="utf-8") as f:
    config = json.load(f)
    # 若有需要在 main 裡直接拿 base_url 也可以這樣讀取
    base_url = config.get("base_url", None)

autodl_conn = AutoDLTTSConnection()
time_engine = None

# -------------------------------------------------------------------
# 3. 主動推播回呼函數 (排程時間到時觸發)
# -------------------------------------------------------------------
async def proactive_trigger_callback(secret_prompt: str):
    logger.info("⏰ 系統時間觸發，正在向大腦請求主動發言...")
    llm_result = await ask_brain_proactive(secret_prompt)
    
    if llm_result.get("action_code") == 1 and manager.active_connections:
        messages = llm_result.get("messages", [])
        for msg in messages:
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

async def screen_monitor_loop():
    """背景視覺監控迴圈 (OpenAI 版 - 含開機冷卻防護與對話優先)"""
    global last_vision_trigger_time
    
    logger.info("👁️ 桌面視覺背景監控已啟動...")
    previous_scene_json = "null"
    last_image = None
    MSE_THRESHOLD = config.get("vision_mse_threshold", 500.0)
    
    # 預設改為 OpenAI 的小模型
    v_model = config.get("sub_model", "gpt-4o-mini")
    cooldown_seconds = config.get("vision_cooldown_seconds", 600)

    while True:
        try:
            await asyncio.sleep(15)

            if config.get("do_not_disturb", False):
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
    autodl_config = config.get("autodl", {})
    if autodl_config:
        logger.info("🚀 正在啟動 AutoDL SSH 隧道...")
        try:
            remote_cmd = "bash -lc 'bash run.sh; bash'"
            autodl_conn.start(
                login_command=autodl_config.get("login_command", ""),
                password=autodl_config.get("password", ""),
                remote_command=remote_cmd, 
                progress=lambda msg: logger.info(f"[AutoDL 狀態]: {msg}")
            )
            logger.info("✅ AutoDL 連線成功！本地 Port 9880 已就緒。")
        except AutoDLConnectionError as e:
            logger.error(f"❌ AutoDL 連線失敗: {e}")
            
    gcal_manager = None
    if config.get("enable_google_calendar"):
        try:
            gcal_manager = GoogleCalendarManager(config.get("gcal_credentials_path", "credentials.json"))
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
    yield 
    
    logger.info("🛑 關閉 AutoDL 連線...")
    autodl_conn.stop()

app = FastAPI(title="Murasame AI Middleware", lifespan=lifespan)
app.mount("/audio", StaticFiles(directory=AUDIO_DIR), name="audio")

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
    if not autodl_conn.is_active():
        logger.warning("AutoDL 未連線，跳過 TTS 生成")
        return "", ""

    import time
    filename = f"response_{int(time.time())}.wav"
    filepath = os.path.join(AUDIO_DIR, filename)
    audio_url = f"http://localhost:8000/audio/{filename}"
    tts_api_url = "http://127.0.0.1:9880/tts" 
    
    params = {
        "text": text_jp,
        "text_lang": "ja", 
        "prompt_lang": "ja",
    }
    folder_name = str(emotion_code)
    try:
        ref_root = "/root/reference_voices"
        ref_audio, prompt_text = autodl_conn.read_reference_metadata(ref_root, folder_name)
        params["ref_audio_path"] = ref_audio
        params["prompt_text"] = prompt_text
    except Exception as e:
        logger.error(f"無法讀取資料夾 {folder_name} 的參考音訊: {e}，取消合成。")
        return "", ""

    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(tts_api_url, json=params)
            if response.status_code >= 400:
                logger.error(f"伺服器退件 (HTTP {response.status_code}): {response.text}")
                return "", ""
            response.raise_for_status()
            with open(filepath, "wb") as f:
                f.write(response.content)
            return filepath, audio_url
    except Exception as e:
        logger.error(f"TTS 生成發生網路或未知錯誤: {e}")
        return "", ""

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
# 6.5 天氣模組 (Weather Module)
# -------------------------------------------------------------------
async def get_weather_async(location: str = "Taipei") -> str:
    try:
        api_url = f"https://zh.wttr.in/{location}?format=3"
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(api_url)
            if response.status_code == 200:
                return response.text.strip()
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
                    logger.info("🔄 主動視覺請求已觸發，重置背景自動監控冷卻時間。")
                    
                    # 預設改為 OpenAI 的小模型
                    v_model = config.get("sub_model", "gpt-4o-mini")

                    screen_description: str = await analyze_screen_async(model_name=v_model)

                    llm_result = await ask_brain(
                        user_input, 
                        time_engine=time_engine, 
                        screen_description=screen_description
                    )

                if llm_result.get("action_code") == 3:
                    logger.info("🌤️ 大腦請求調用天氣資訊...")
                    
                    target_location = config.get("weather_location", "Taipei")
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