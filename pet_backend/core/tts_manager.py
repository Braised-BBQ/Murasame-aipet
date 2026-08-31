import os
import time
import logging
import asyncio
import subprocess
import httpx
from pathlib import Path
from typing import Tuple, Optional, Any

logger = logging.getLogger("PetMiddleware")

# 自動定位專案根目錄 (從 pet_backend/core/tts_manager.py 往上推三層)
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

def _resolve_path(path_str: str) -> Path:
    """自動處理路徑：絕對路徑直接套用，相對路徑則以專案根目錄為起點"""
    p = Path(path_str).expanduser()
    if p.is_absolute():
        return p
    return (PROJECT_ROOT / p).resolve()

class TTSManager:
    def __init__(self, autodl_conn: Any, audio_dir: str) -> None:
        self.autodl_conn = autodl_conn
        self.audio_dir: str = audio_dir
        self.local_process: Optional[subprocess.Popen[bytes]] = None
        self.base_url: str = "http://127.0.0.1:9880"
        self.mode: str = "local"
        self.model_dir: Optional[Path] = None

    async def start(self, config_manager: Any) -> bool:
        """根據設定檔啟動 TTS 背景服務 (支援本地或 AutoDL)"""
        self.mode = str(config_manager.get("tts_mode", "local"))
        logger.info(f"🔄 準備啟動 TTS 服務，當前模式: [{self.mode.upper()}]")
        
        if self.mode == "autodl":
            autodl_config: dict[str, str] = config_manager.get("autodl", {})
            if not autodl_config:
                logger.error("未設定 AutoDL 參數，無法啟動。")
                return False
                
            # 明確宣告型別的內部函式，取代 Lambda 讓 Pylance 閉嘴
            def _log_progress(msg: Any) -> None:
                logger.info(f"[AutoDL]: {str(msg)}")

            try:
                self.autodl_conn.start(
                    login_command=autodl_config.get("login_command", ""),
                    password=autodl_config.get("password", ""),
                    remote_command="bash -lc 'bash run.sh; bash'",
                    progress=_log_progress
                )
            except Exception as e:
                logger.error(f"AutoDL 啟動失敗: {e}")
                return False
                
        elif self.mode == "local":
            # 讀取設定並透過 _resolve_path 轉換，現在你可以放心寫相對路徑了
            engine_str = str(config_manager.get("tts_engine_root", ""))
            model_str = str(config_manager.get("tts_model_dir", ""))
            
            engine_root = _resolve_path(engine_str)
            self.model_dir = _resolve_path(model_str)
            
            if not engine_root.exists() or not self.model_dir.exists():
                logger.error(
                    f"❌ 本地 TTS 路徑錯誤！\n"
                    f"引擎路徑解析為: {engine_root}\n"
                    f"模型路徑解析為: {self.model_dir}"
                )
                return False
                
            api_script = engine_root / "api_v2.py"
            config_path = engine_root / "GPT_SoVITS" / "configs" / "tts_infer.yaml"
            
            # 加入這一行，動態定位整合包內的 python.exe
            python_exe = engine_root / "runtime" / "python.exe" 
            
            cmd = [
                str(python_exe), str(api_script), # 將原本的 sys.executable 換成 str(python_exe)
                "-a", "127.0.0.1", "-p", "9880", "-c", str(config_path)
            ]
            
            env = os.environ.copy()
            env["PYTHONUTF8"] = "1"
           
            try:
                # 【修改這裡】不要再隱藏輸出了，讓報錯直接顯示在你的終端機
                self.local_process = subprocess.Popen(
                    cmd, 
                    cwd=str(engine_root), 
                    env=env
                )
                logger.info("✅ 本地 GPT-SoVITS 子進程已啟動。")
            except Exception as e:
                logger.error(f"❌ 本地 TTS 啟動失敗: {e}")
                return False
# 如果是 AutoDL 模式，恢復舊版行為：直接放行，不執行嚴格等待
        if self.mode == "autodl":
            logger.info("✅ AutoDL SSH 隧道已建立！雲端 GPT-SoVITS 將在背景啟動...")
            return True
            
        # 如果是 Local 模式，才需要等待本地 API 啟動與掛載權重
        if await self._wait_for_api():
            if self.mode == "local":
                await self._load_local_weights()
            return True
        return False

    async def _wait_for_api(self, timeout: float = 45.0) -> bool:
        """等待 FastAPI 伺服器啟動"""
        logger.info("⏳ 正在等待 TTS API 就緒...")
        start_time = time.time()
        async with httpx.AsyncClient(timeout=2.0) as client:
            while time.time() - start_time < timeout:
                try:
                    resp = await client.get(self.base_url)
                    if resp.status_code in [200, 404]:
                        logger.info("✅ TTS API 伺服器已成功連線！")
                        return True
                except httpx.RequestError:
                    pass
                await asyncio.sleep(1.5)
        logger.error("❌ TTS API 等待超時！")
        return False

    async def _load_local_weights(self) -> None:
        """本地模式下，動態掛載權重"""
        if self.model_dir is None:
            return
            
        logger.info("⚙️ 正在載入模型權重...")
        gpt_weight = self.model_dir / "murasame-gpt.ckpt"
        sovits_weight = self.model_dir / "murasame-sovits.pth"
        
        async with httpx.AsyncClient(timeout=60.0) as client:
            try:
                await client.get(f"{self.base_url}/set_gpt_weights", params={"weights_path": str(gpt_weight)})
                await client.get(f"{self.base_url}/set_sovits_weights", params={"weights_path": str(sovits_weight)})
                logger.info("✅ 本地模型權重掛載完成！")
            except Exception as e:
                logger.error(f"❌ 本地模型權重掛載失敗: {e}")

    def stop(self) -> None:
        """關閉服務"""
        logger.info("🛑 關閉目前的 TTS 服務...")
        if self.mode == "autodl" and hasattr(self.autodl_conn, 'is_active') and self.autodl_conn.is_active():
            self.autodl_conn.stop()
        elif self.mode == "local" and self.local_process:
            self.local_process.terminate()
            try:
                self.local_process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.local_process.kill()
            self.local_process = None

    async def generate(self, text_jp: str, emotion_code: int = 5) -> Tuple[str, str]:
        """統一的語音合成入口"""
        if self.mode == "autodl" and hasattr(self.autodl_conn, 'is_active') and not self.autodl_conn.is_active():
            logger.warning("AutoDL 未連線，跳過 TTS")
            return "", ""
        if self.mode == "local" and not self.local_process:
            logger.warning("本地 TTS 未啟動，跳過 TTS")
            return "", ""

        folder_name = str(emotion_code)
        filename = f"response_{int(time.time())}.wav"
        filepath = os.path.join(self.audio_dir, filename)
        audio_url = f"http://localhost:8000/audio/{filename}"
        
        params: dict[str, Any] = {
            "text": text_jp,
            "text_lang": "ja", 
            "prompt_lang": "ja",
            "split_bucket": True      # 建議開啟分倉處理，有助於降低 VRAM 佔用
        }

        try:
            if self.mode == "autodl":
                ref_root = "/root/reference_voices"
                ref_audio, prompt_text = self.autodl_conn.read_reference_metadata(ref_root, folder_name)
            else:
                if self.model_dir is None:
                    raise ValueError("本地模型路徑未初始化")
                    
                ref_dir = self.model_dir / "reference_voices" / folder_name
                prompt_text = (ref_dir / "asr.txt").read_text(encoding="utf-8").strip()
                
                audio_files = [p for p in ref_dir.iterdir() if p.suffix.lower() in {".wav", ".mp3", ".flac"}]
                if not audio_files:
                    raise FileNotFoundError("資料夾內無音檔")
                ref_audio = str(audio_files[0].resolve())

            params["ref_audio_path"] = ref_audio
            params["prompt_text"] = prompt_text
        except Exception as e:
            logger.error(f"❌ 無法讀取情緒 [{folder_name}] 的參考音訊: {e}")
            return "", ""

        try:
            async with httpx.AsyncClient(timeout=120.0) as client:
                response = await client.post(f"{self.base_url}/tts", json=params)
                response.raise_for_status()
                with open(filepath, "wb") as f:
                    f.write(response.content)
                return filepath, audio_url
        except Exception as e:
            logger.error(f"❌ TTS 推理發生錯誤: {e}")
            return "", ""