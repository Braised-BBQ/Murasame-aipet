import json
import os
import logging
import asyncio
import wave
import contextlib
from typing import Any
from fastapi import FastAPI, WebSocket, WebSocketDisconnect,UploadFile, File
from fastapi.staticfiles import StaticFiles
import uvicorn
from contextlib import asynccontextmanager
from core.vision import analyze_screen_async
from core.brain import ask_brain, ask_brain_proactive, memory
from core.time_engine import TimeEngine
from core.vision import capture_screen_image, calculate_image_mse, detect_screen_changes_async, analyze_screen_async
import time
from logging.handlers import RotatingFileHandler
import glob
from core.config_manager import config_manager
from core.autodl_tts import AutoDLTTSConnection
from core.tts_manager import TTSManager  # 引入新的管理器
from core.weather import get_weather_async
from core.weather import get_current_location_async
from core.mcp_manager import mcp_manager
from openai import AsyncOpenAI
import base64
import tempfile
from pydub import AudioSegment # type: ignore
from typing import cast, Any

# -------------------------------------------------------------------
# 設定 FFmpeg 絕對路徑 (供 pydub 轉檔使用)
# -------------------------------------------------------------------
PROJECT_ROOT = os.path.dirname(__file__)
ffmpeg_path = os.path.abspath(os.path.join(PROJECT_ROOT, "bin", "ffmpeg.exe"))
ffprobe_path = os.path.abspath(os.path.join(PROJECT_ROOT, "bin", "ffprobe.exe"))

# 強制告訴 pydub 使用我們透過 setup.bat 下載在 bin 裡面的執行檔
AudioSegment.converter = ffmpeg_path
AudioSegment.ffprobe = ffprobe_path # type: ignore

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
            
            # 即時檢查最新的勿擾模式 (第一級以上：禁止桌面自動視覺)
            dnd_level = int(config_manager.get("do_not_disturb", 0))
            if dnd_level >= 1:
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
                    f"請以叢雨的身份，對此變化發表 2~3 句簡短的評論、吐槽或關心。"
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

    time_engine = TimeEngine(
        collection=memory.collection, 
        brain_api_callback=proactive_trigger_callback
    )
    logger.info("✅ 時間模組 (TimeEngine) 已啟動！")

    monitor_task = asyncio.create_task(screen_monitor_loop())
    logger.info("✅ 視覺監控背景任務已掛載！")
    # 🌟 新增這裡：啟動時在背景執行一次記憶體檢與濃縮
    asyncio.create_task(memory.consolidate_memories())
    logger.info("🧠 記憶整併背景任務已觸發！")
    # ==========================================
    # 🌟 新增：讀取設定檔決定是否連線 MCP (Stdio 模式)
    # ==========================================
    enable_mcp = config_manager.get("enable_mcp", False)
    
    if enable_mcp:
        logger.info("🔌 設定檔已啟用 MCP，正在讀取 mcp_servers.json 啟動服務...")
        # 👉 直接呼叫，不需要傳入任何 URL 參數
        await mcp_manager.initialize()
    else:
        logger.info("⚠️ MCP 功能已在設定中關閉。")
    # ==========================================
    # 🚀 分水嶺：伺服器準備好，開始接受前端請求
    # ==========================================
    yield 
    
    # ==========================================
    # 3. 關閉時：執行清理工作
    # ==========================================
    logger.info("🛑 關閉 TTS 服務...")
    tts_manager.stop()
    logger.info("🛑 關閉 MCP 連線...")
    await mcp_manager.shutdown()
    
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
        
        if success:
            logger.info("✅ TTS 熱修改切換成功！")
            # 💡 修改點：把這裡的 return 刪除，讓程式繼續往下走
        else:
            logger.error("❌ TTS 熱修改失敗，請檢查終端機報錯。")
            # 失敗時提早結束並回傳錯誤，這個 return 保留是正確的
            return {"status": "error", "message": "TTS 啟動失敗，請檢查終端機"}
            
    except Exception as e:
        logger.error(f"⚠️ 熱修改時 TTS 切換失敗: {e}")
        # 發生例外時提早結束，這個 return 保留是正確的
        return {"status": "error", "message": f"設定已生效，但 TTS 啟動發生異常: {e}"}
    
    # --- 👇 因為上面成功時沒有 return，程式現在可以順利執行到這裡 👇 ---

    enable_mcp = config_manager.get("enable_mcp", False)
    
    # 先斷開舊連線
    try:
        await mcp_manager.shutdown()
    except RuntimeError as e:
        if "Attempted to exit cancel scope in a different task" in str(e):
            logger.warning("⚠️ 攔截到跨任務關閉警告，已強制釋放舊的 MCP 資源。")
            # 👇 修正這裡：給它一個全新的 AsyncExitStack，而不是 None
            mcp_manager.exit_stack = contextlib.AsyncExitStack() 
        else:
            raise e
    except Exception as e:
        logger.error(f"⚠️ 關閉舊 MCP 連線時發生異常: {e}")
        # 👇 保險起見，其他未預期錯誤也給它一個新池子
        mcp_manager.exit_stack = contextlib.AsyncExitStack() 
    
    if enable_mcp:
        logger.info("🔌 MCP 設定更新，重新讀取 mcp_servers.json 啟動服務...")
        await mcp_manager.initialize()
        
    return {"status": "success", "message": "設定已熱修改生效"}
@app.get("/api/current_location")
async def get_current_location_api():
    """提供給前端 settings.html 讀取目前的實際定位"""
    try:
        current_loc = await get_current_location_async()
        return {"status": "success", "location": current_loc}
    except Exception as e:
        logger.error(f"前端請求定位失敗: {e}")
        return {"status": "error", "location": "未知"}

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
# 5.5 STT 語音辨識功能 (新增在這裡)
# -------------------------------------------------------------------
# 取得 api_key 與 base_url（確保同時支援 OpenAI 或相容於 OpenAI 格式的 Gemini 代理端點）
raw_key = config_manager.get("openai_api_key", config_manager.get("api_key", ""))
api_key = raw_key if raw_key else "sk-dummy-key"
base_url = config_manager.get("base_url", None)

# 建立專屬的 STT 客戶端（同時帶入 api_key 與 base_url）
stt_client = AsyncOpenAI(
    api_key=api_key,
    base_url=base_url
)
@app.post("/api/stt")
async def speech_to_text(audio_file: UploadFile = File(...)) -> dict[str, str]:
    if not config_manager.get("enable_voice_chat", True):
        logger.info("🚫 收到語音請求，但語音對話功能已在設定中關閉。")
        return {"status": "disabled", "text": ""}

    logger.info(f"🎤 收到語音檔案，正在進行格式轉換與多模態解析...")
    
    # 1. 建立暫存檔存放接收到的 webm
    with tempfile.NamedTemporaryFile(delete=False, suffix=".webm") as webm_tmp:
        content = await audio_file.read()
        webm_tmp.write(content)
        webm_path = webm_tmp.name

    wav_path = webm_path.replace(".webm", ".wav")

    try:
        # 2. 使用 pydub 將 webm 轉為 API 支援的 wav 格式
        audio_segment = AudioSegment.from_file(webm_path, format="webm") # type: ignore
        audio_segment.export(wav_path, format="wav") # type: ignore

        # 3. 讀取轉好的 wav 檔案並轉成 Base64
        with open(wav_path, "rb") as wav_file:
            wav_content = wav_file.read()
            audio_base64 = base64.b64encode(wav_content).decode('utf-8')

        # 4. 餵給多模態大腦模型
        payload_messages = cast(Any, [
            {
                "role": "user",
                "content": [
                    {
                        "type": "text", 
                        "text": "請將這段語音轉成文字。如果裡面有說話，請直接回傳你聽到的文字內容，不要額外加上標點符號或回覆其他話語。"
                    },
                    {
                        "type": "input_audio",
                        "input_audio": {
                            "data": audio_base64,
                            "format": "wav"  # 這裡宣告格式為 wav，完美符合代理端點的要求！
                        }
                    }
                ]
            }
        ])
        
        response = await stt_client.chat.completions.create(
            model=str(config_manager.get("sub_model", "gpt-4o-mini")),
            messages=payload_messages
        )
        
        recognized_text = str(response.choices[0].message.content or "")
        logger.info(f"✅ 多模態語音解析結果: {recognized_text}")
        return {"status": "success", "text": recognized_text}
        
    except Exception as e:
        logger.error(f"❌ 語音解析或轉檔失敗: {e}")
        return {"status": "error", "text": ""}
    finally:
        # 5. 清理殘留的暫存檔
        if os.path.exists(webm_path):
            os.remove(webm_path)
        if os.path.exists(wav_path):
            os.remove(wav_path)
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
                    
                    # 💡 提取大腦給予的特別關注指示 (若無則為預設空字串)
                    focus_instruction = llm_result.get("vision_focus", "")
                    if focus_instruction:
                        logger.info(f"🎯 大腦特別指示視覺模組關注: {focus_instruction}")
                    
                    last_vision_trigger_time = time.time()
                    v_model = config_manager.get("sub_model", "gemini-3.5-flash-lite")

                    # 💡 將 focus_instruction 傳遞給視覺模組
                    screen_description: str = await analyze_screen_async(
                        model_name=v_model, 
                        focus_instruction=focus_instruction
                    )

                    llm_result = await ask_brain(
                        user_input, 
                        time_engine=time_engine, 
                        screen_description=screen_description
                    )
                mcp_call_count = 0
                MAX_MCP_CALLS = 10

                while llm_result.get("action_code") == 4:
                    if mcp_call_count >= MAX_MCP_CALLS:
                        logger.warning("⚠️ 達到 MCP 連續調用上限，強制中斷，避免死迴圈！")
                        # 強制塞入錯誤提示，逼迫她停止使用工具並說話 (action_code: 1)
                        llm_result = await ask_brain(
                            user_input, 
                            time_engine=time_engine, 
                            mcp_info="【系統強制提示】：你連續使用工具太多次或一直失敗，請立即停止呼叫工具，直接向主人說明你找不到或無法播放該歌單。"
                        )
                        break # 👈 跳出迴圈
                        
                    mcp_call_count += 1
                    logger.info(f"🛠️ 大腦請求調用 MCP 工具... (第 {mcp_call_count} 次)")
                    
                    tool_name = str(llm_result.get("mcp_tool_name", ""))
                    tool_args = dict(llm_result.get("mcp_tool_args", {}))
                    
                    if tool_name:
                        tool_result_text = await mcp_manager.execute_tool(tool_name, tool_args)
                        logger.info(f"🛠️ MCP 工具執行完畢，結果長度: {len(tool_result_text)}")

                        llm_result = await ask_brain(
                            user_input, 
                            time_engine=time_engine, 
                            mcp_info=tool_result_text  
                        )
                    else:
                        break
                # 🌟 天氣模組調用簡化：直接呼叫 get_weather_async()
                if llm_result.get("action_code") == 3:
                    logger.info("🌤️ 大腦請求調用天氣資訊...")
                    
                    # 嘗試從大腦的 JSON 中抓取 "target_location" (如果大腦沒給，就會是 None)
                    asked_location = llm_result.get("target_location")
                    
                    # 將地點傳入天氣模組 (如果是 None，weather.py 會自己切換成本地)
                    weather_info: str = await get_weather_async(custom_location=asked_location)
                    logger.info(f"🌤️ 取得天氣結果:\n{weather_info}")

                    # 將包含「當前數據 + 未來 3 天預報」的天氣資料傳回大腦
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
    uvicorn.run("main:app", host="127.0.0.1", port=8000, reload=False)